"""
Tool Executor - 公共工具执行组件

提供可复用的工具执行逻辑，包括：
- LoopDetector: 循环检测
- ToolExecutor: 工具执行器
- 上下文压缩函数
"""

import asyncio
import hashlib
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class LoopDetector:
    """检测 Agent 执行循环

    通过跟踪最近的工具调用和输出来检测是否陷入重复模式
    """
    max_history: int = 10  # 跟踪的历史条目数
    similar_threshold: int = 5  # 连续相似条目数达到这个值认为循环
    output_similarity: float = 0.8  # 输出相似度阈值

    # 跟踪历史
    _tool_call_history: deque = field(default_factory=deque)
    _output_history: deque = field(default_factory=deque)

    def _hash_args(self, args: dict) -> str:
        """对工具参数进行哈希，用于比较是否相同"""
        if not args:
            return ""
        # 简化：只取前3个键值对 + 整体大小
        items = list(args.items())[:3]
        short = {k: str(v)[:50] for k, v in items}
        return str(sorted(short.items()))

    def _hash_output(self, output: str) -> str:
        """对输出进行哈希"""
        if not output:
            return ""
        # 取输出的前100字符的哈希
        return hashlib.md5(output[:200].encode()).hexdigest()[:8]

    def record_tool_call(self, tool_name: str, args: dict):
        """记录一个工具调用"""
        entry = (tool_name, self._hash_args(args))
        self._tool_call_history.append(entry)
        if len(self._tool_call_history) > self.max_history:
            self._tool_call_history.popleft()

    def record_output(self, output: str):
        """记录一个输出"""
        if output and len(output) > 20:  # 太短的输出不计入
            entry = self._hash_output(output)
            self._output_history.append(entry)
            if len(self._output_history) > self.max_history:
                self._output_history.popleft()

    def detect_loop(self) -> tuple[bool, str]:
        """
        检测是否陷入循环

        Returns:
            (is_looping, reason)
        """
        # 检查是否有连续的相同工具调用
        if len(self._tool_call_history) >= self.similar_threshold:
            recent = list(self._tool_call_history)[-self.similar_threshold:]
            if len(set(recent)) == 1:
                tool_name = recent[0][0]
                return True, f"检测到重复工具调用: 连续 {self.similar_threshold} 次调用相同的 '{tool_name}'"

        # 检查是否有连续的相似输出
        if len(self._output_history) >= self.similar_threshold:
            recent = list(self._output_history)[-self.similar_threshold:]
            if len(set(recent)) == 1:
                return True, f"检测到重复输出: 连续 {self.similar_threshold} 次产生相同的输出"

        return False, ""


def should_compress(messages: list, max_context_window: int = 200 * 1024, threshold: float = 0.7) -> bool:
    """检查上下文是否需要压缩

    Args:
        messages: 消息列表
        max_context_window: 最大上下文窗口（默认 200K tokens）
        threshold: 压缩阈值（默认 70%）

    Returns:
        True if compression is recommended
    """
    if not messages:
        return False

    # 简单估算：假设平均每个字符约 0.25 tokens
    total_chars = sum(len(m.get("content", "")) for m in messages)
    estimated_tokens = total_chars // 4

    threshold_tokens = int(max_context_window * threshold)
    return estimated_tokens >= threshold_tokens


async def compress_context_llm(
    messages: list,
    model_adapter,
    max_context_window: int = 200 * 1024
) -> bool:
    """使用 LLM 智能压缩上下文

    将所有非 system 消息交给 LLM 提炼精简信息，
    然后用总结替代所有非 system 消息。

    Args:
        messages: 消息列表（会被直接修改）
        model_adapter: 模型适配器
        max_context_window: 最大上下文窗口

    Returns:
        True if compression succeeded
    """
    if not messages:
        return False

    # 分离 system 和非 system 消息
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system_msgs = [m for m in messages if m.get("role") != "system"]

    if len(non_system_msgs) <= 2:
        return False

    # 格式化消息给 LLM
    conversation = "\n".join([
        f"{m.get('role', 'user')}: {m.get('content', '')[:500]}"
        for m in non_system_msgs
    ])

    summarize_prompt = f"""你是一个上下文压缩助手。请提炼以下对话的精简摘要，
保留关键信息、决策、进展和重要细节。摘要应该简洁但信息完整。

对话内容：
{conversation}

请直接返回摘要内容，不需要额外解释。摘要格式：
[对话摘要]
- 关键主题：xxx
- 重要进展：xxx
- 待处理事项：xxx
- 关键细节：xxx
"""

    try:
        # 调用 LLM 生成摘要
        response = await model_adapter.chat(
            [{"role": "user", "content": summarize_prompt}],
            ""
        )

        if not response:
            logger.warning("[compress_context_llm] LLM 摘要返回为空，压缩失败")
            return False

        # 用摘要替换非 system 消息
        summary_msg = {"role": "system", "content": f"[对话摘要]\n{response}"}
        messages[:] = system_msgs + [summary_msg]

        original_tokens = sum(len(m.get("content", "")) for m in non_system_msgs) // 4
        summary_tokens = len(response) // 4
        logger.info(f"[compress_context_llm] 压缩完成：原始约 {original_tokens} tokens → 摘要 {summary_tokens} tokens")
        return True

    except Exception as e:
        logger.error(f"[compress_context_llm] 压缩失败: {e}")
        return False


class ToolExecutor:
    """公共工具执行器

    提供 LLM 调用和工具执行的公共逻辑，可被不同模式复用。
    """

    def __init__(
        self,
        adapter,
        tool_registry,
        protocol_tools: Optional[Any] = None
    ):
        """
        Args:
            adapter: 模型适配器
            tool_registry: 工具注册表
            protocol_tools: 协议工具（可选）
        """
        self.adapter = adapter
        self.tool_registry = tool_registry
        self.protocol_tools = protocol_tools

    async def call_llm(
        self,
        messages: list,
        tools: list,
        system_prompt: str
    ) -> tuple[str, list[dict], Optional[str]]:
        """调用 LLM

        Args:
            messages: 消息列表
            tools: 工具 schema 列表
            system_prompt: 系统提示词

        Returns:
            (response_text, tool_calls, stop_reason)
        """
        result = await self.adapter.chat_with_tools_and_stop_reason(
            messages, tools, system_prompt
        )
        return result.text, result.tool_calls, result.stop_reason

    def get_tools_schema(self) -> list:
        """获取工具 schema

        Returns:
            工具 schema 列表（内置 + 协议工具）
        """
        tools = self.tool_registry.get_tools_schema()
        if self.protocol_tools:
            tools = tools + self.protocol_tools.get_all_schemas()
        return tools

    async def execute_tools_parallel(
        self,
        tool_calls: list[dict],
        execute_tool_fn: Callable[[dict], tuple]
    ) -> list[tuple[dict, Any]]:
        """并行执行工具

        使用 DependencyAnalyzer 分析依赖关系，将工具分组并行执行。

        Args:
            tool_calls: 工具调用列表
            execute_tool_fn: 执行单个工具的函数，接收 tool_call dict，返回 (tool_call, result)

        Returns:
            [(tool_call, result), ...] 列表
        """
        from src.tools.dependency_analyzer import DependencyAnalyzer

        # Generate unique ids for tool_calls that don't have one
        for idx, tc in enumerate(tool_calls):
            if not tc.get("id"):
                tc["id"] = f"auto_{idx}_{tc.get('name', 'unknown')}"

        analyzer = DependencyAnalyzer()
        batches = analyzer.analyze(tool_calls)

        results = []

        # Execute each batch
        for batch_idx, batch in enumerate(batches):
            if len(batch) == 1:
                # Single tool - execute directly
                result = await execute_tool_fn(batch[0])
                results.append((batch[0], result))
            else:
                # Multiple tools - execute in parallel
                tasks = [execute_tool_fn(tc) for tc in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                # Filter out exceptions and log them
                for i, result in enumerate(batch_results):
                    if isinstance(result, Exception):
                        logger.error(f"并行工具执行异常: {result}")
                        batch_results[i] = (batch[i], {"error": str(result)})
                results.extend(batch_results)

        # Sort results to maintain original order
        original_order = {tc["id"]: idx for idx, tc in enumerate(tool_calls)}
        results.sort(key=lambda x: original_order.get(x[0].get("id"), 0))

        return results

    async def execute_tools_serial(
        self,
        tool_calls: list[dict],
        execute_tool_fn: Callable[[dict], tuple],
        before_execute: Optional[Callable[[dict], None]] = None,
        after_execute: Optional[Callable[[dict, Any], None]] = None
    ) -> list[tuple[dict, Any]]:
        """串行执行工具

        适用于需要串行执行或需要特殊处理（如 idle、路径验证）的场景。

        Args:
            tool_calls: 工具调用列表
            execute_tool_fn: 执行单个工具的函数
            before_execute: 执行前回调，接收 tool_call
            after_execute: 执行后回调，接收 (tool_call, result)

        Returns:
            [(tool_call, result), ...] 列表
        """
        results = []
        for tc in tool_calls:
            if before_execute:
                before_execute(tc)

            result = await execute_tool_fn(tc)

            if after_execute:
                after_execute(tc, result)

            results.append((tc, result))

        return results
