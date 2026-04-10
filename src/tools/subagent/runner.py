"""Subagent runner - executes subagent tasks in isolated context"""
import asyncio
from typing import Optional

from src.agent.context import create_context, AgentContext
from src.adapters.base import ModelAdapter
from src.tools.registry import ToolRegistry
from src.tools.orchestrator import ToolOrchestrator
from src.tools.context import ToolContext, ToolGate
from src.utils import get_logger

from .models import SubagentConfig, SubagentResult

logger = get_logger("subagent.runner")


class SubagentRunner:
    """Executes a subagent task with isolated context"""

    def __init__(
        self,
        config: SubagentConfig,
        adapter: ModelAdapter,
        tool_registry: ToolRegistry,
    ):
        self.config = config
        self.adapter = adapter
        self.tool_registry = tool_registry
        self._filtered_registry: Optional[ToolRegistry] = None

    def _create_filtered_registry(self) -> ToolRegistry:
        """Create a tool registry with only allowed tools, excluding denied tools"""
        filtered = ToolRegistry()

        # 获取所有可用的工具（始终排除嵌套 subagent 和 team）
        available_tools = {
            name: tool for name, tool in self.tool_registry.tools.items()
            if name not in ("subagent", "team")
        }

        if not self.config.allowed_tools and not self.config.denied_tools:
            # 无限制：使用完整注册表
            for name, tool in available_tools.items():
                filtered.register(tool)
            return filtered

        if self.config.allowed_tools:
            # 白名单模式：只包含允许的工具
            for tool_name in self.config.allowed_tools:
                if tool_name in available_tools:
                    filtered.register(available_tools[tool_name])
        else:
            # 黑名单模式：包含所有工具，排除 denied_tools
            for name, tool in available_tools.items():
                if name not in self.config.denied_tools:
                    filtered.register(tool)

        return filtered

    async def _compress_context_llm(self, context: AgentContext) -> bool:
        """使用 LLM 智能压缩上下文。单一实现在 LLMContextCompressor。

        Args:
            context: AgentContext to compress

        Returns:
            True if compression succeeded
        """
        from src.context.core import LLMContextCompressor
        return await LLMContextCompressor.compress_context(context, self.adapter)

    def _create_isolated_context(self) -> AgentContext:
        """Create an isolated AgentContext for this subagent"""
        context = create_context(
            max_iterations=self.config.max_iterations,
            timeout_seconds=self.config.timeout_seconds,
        )

        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        time_info = f"""## 当前时间
当前系统时间: {current_time}
请注意：回答涉及时间的问题时，应以该时间为准。"""

        full_system_prompt = f"{time_info}\n\n{self.config.system_prompt}"
        context.add_system_message(full_system_prompt)

        return context

    async def run(self, prompt: str) -> SubagentResult:
        """Run subagent with AgentLoop for iteration control.

        Uses AgentLoop for unified iteration management while keeping
        SubagentRunner's unique features: LLM compression, confirmation check,
        tool filtering, and nested subagent prevention.
        """
        context = self._create_isolated_context()
        context.add_user_message(prompt)

        # Create filtered tool registry
        filtered_registry = self._create_filtered_registry()
        tool_gate = ToolGate()
        tool_orchestrator = ToolOrchestrator(gate=tool_gate)

        # Get tool schemas for LLM
        tools = filtered_registry.get_tools_schema()

        # Get system prompt from context
        system_prompt = ""
        if context.short_term_memory:
            system_prompt = context.short_term_memory[0].content

        messages = context.get_messages_for_api()

        logger.info(
            f"[SubagentRunner] 开始执行 (AgentLoop模式) | max_iterations={context.state.max_iterations} | "
            f"可用工具={[t['name'] for t in tools]}"
        )

        tool_call_history = []
        iterations = 0

        async def _check_confirmation(response: str, stop_reason: str) -> Optional[bool]:
            """Handle completion confirmation check."""
            nonlocal iterations

            if stop_reason != "stop":
                return None  # Skip confirmation if not natural stop

            logger.info(f"[SubagentRunner] 模型自然停止，发送完成确认请求")

            # Add confirmation request to context
            context.add_user_message(
                "请确认：你是否已完成了上述任务？只需简单回答'已完成'或'未完成'，如果未完成请说明原因。"
            )
            confirm_messages = context.get_messages_for_api()

            # Call LLM for confirmation
            confirm_result = await self.adapter.chat_with_tools_and_stop_reason(
                messages=confirm_messages,
                tools=[],
                system_prompt=""
            )
            confirm_response = confirm_result.text if confirm_result.text else ""

            logger.info(f"[SubagentRunner] 确认响应: {confirm_response[:200] if confirm_response else '(空)'}")

            if "完成" in confirm_response or "已完成" in confirm_response:
                logger.info(f"[SubagentRunner] 确认完成，结束执行")
                return True
            else:
                logger.info(f"[SubagentRunner] 确认未完成，继续迭代")
                if confirm_response:
                    context.add_assistant_message(f"[系统确认回复]: {confirm_response}")
                return False

        async def execute_fn():
            """Execute one iteration: LLM call + tool execution."""
            nonlocal messages, system_prompt, iterations, tool_call_history

            iterations += 1

            # Context compression check
            # Tier-2: micro-compact older tool results before threshold check
            from src.context.micro_compactor import micro_compact_messages
            micro_compact_messages(context.short_term_memory, keep_recent=3)

            current_tokens = context.calculate_total_tokens()
            if context.should_compress(current_tokens):
                logger.warning(f"[SubagentRunner] 上下文超过70%阈值 ({current_tokens} tokens)，开始压缩")
                success = await self._compress_context_llm(context)
                if success:
                    logger.info(f"[SubagentRunner] LLM 压缩成功")
                else:
                    # Fallback to simple compression
                    msgs = context.short_term_memory
                    system_msgs = [m for m in msgs if m.role == "system"]
                    recent_msgs = [m for m in msgs if m.role != "system"][-10:]
                    context.short_term_memory = system_msgs + recent_msgs
                    logger.info(f"[SubagentRunner] 简单压缩完成")
                messages = context.get_messages_for_api()
                logger.info(f"[SubagentRunner] 压缩完成，当前消息数: {len(messages)}")

            logger.info(f"[SubagentRunner] ===== 迭代 {iterations} 开始 =====")

            # LLM call
            result = await self.adapter.chat_with_tools_and_stop_reason(
                messages=messages,
                tools=tools,
                system_prompt=system_prompt
            )
            response = result.text
            tool_calls = result.tool_calls
            stop_reason = result.stop_reason

            logger.info(
                f"[SubagentRunner] LLM 响应 | 迭代={iterations} | "
                f"response长度={len(response) if response else 0} | "
                f"tool_calls数量={len(tool_calls) if tool_calls else 0} | "
                f"stop_reason={stop_reason}"
            )

            # If no tool calls, return early for confirmation check
            if not tool_calls:
                return (response, [], stop_reason)

            # Execute tool calls
            context.add_assistant_message(response)

            for tc in tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                tool_id = tc.get("id", f"tc_{iterations}_{tool_name}")

                # Skip nested subagent calls to prevent infinite recursion
                if tool_name == "subagent":
                    skip_message = (
                        "[警告] 检测到嵌套子代理调用，已被系统阻止以防止无限递归。"
                        "子代理无法调用其他子代理。"
                    )
                    logger.warning(f"[SubagentRunner] 跳过嵌套 subagent 调用: {tool_args.get('prompt', '')[:50]}...")
                    context.add_tool_message(skip_message, tool_name="subagent")

                    # Record in tool_call_history to maintain consistency
                    tool_call_history.append({
                        "id": tool_id,
                        "name": tool_name,
                        "arguments": tool_args,
                        "skipped": True,
                        "skip_reason": "nested_subagent_blocked"
                    })
                    continue
                tool_call_history.append({
                    "id": tool_id,
                    "name": tool_name,
                    "arguments": tool_args,
                })

                logger.info(f"[SubagentRunner] 执行工具: {tool_name}")
                try:
                    tool_context = ToolContext(
                        tool_name=tool_name,
                        args=tool_args,
                        cwd=None,
                        tracker=None,
                        gate=tool_gate,
                        metadata={},
                    )
                    result = await tool_orchestrator.execute(
                        tool=filtered_registry.get(tool_name),
                        args=tool_args,
                        context=tool_context,
                    )
                    result_str = str(result) if result is not None else ""
                    # Tier-1: persist large tool outputs to disk, keep preview
                    from src.context.tool_persister import persist_tool_output
                    preview = persist_tool_output(tc.get("id", f"tool_{tool_name}"), result_str)
                    logger.info(f"[SubagentRunner] 工具 {tool_name} 执行完成")
                    context.add_tool_message(preview, tool_name=tool_name)
                except Exception as e:
                    error_msg = f"Error: {str(e)}"
                    logger.error(f"[SubagentRunner] 工具 {tool_name} 执行失败: {e}")
                    context.add_tool_message(error_msg, tool_name=tool_name)

            # Update messages for next iteration
            messages = context.get_messages_for_api()

            return (response, tool_calls, stop_reason)

        # Create AgentLoop with confirmation check
        from src.agent.loop import AgentLoop
        loop = AgentLoop(
            context=context,
            max_iterations=self.config.max_iterations,
            on_confirmation_check=_check_confirmation,
        )

        try:
            final_response = await loop.run(execute_fn)
            # loop.run() returns the final response when loop completes
            # Get final state
            iterations = loop.state.iteration
            # Use context's accurate token calculation
            total_tokens = context.calculate_total_tokens()
            return SubagentResult(
                success=True,
                output=final_response if final_response else "[无输出]",
                tool_calls=tool_call_history,
                iterations=iterations,
                tokens_used=total_tokens,
            )
        except asyncio.TimeoutError:
            total_tokens = context.calculate_total_tokens()
            return SubagentResult(
                success=False,
                output="",
                tool_calls=tool_call_history,
                iterations=iterations,
                tokens_used=total_tokens,
                error="Subagent execution timed out",
            )
        except Exception as e:
            logger.error(f"Subagent execution failed: {e}")
            total_tokens = context.calculate_total_tokens()
            return SubagentResult(
                success=False,
                output="",
                tool_calls=tool_call_history,
                iterations=iterations,
                tokens_used=total_tokens,
                error=str(e),
            )

__all__ = ["SubagentRunner"]
