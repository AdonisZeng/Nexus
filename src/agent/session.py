"""AgentSession - execution engine separated from CLI UI.

Extracted from NexusCLI to allow non-CLI usage (API, web, tests).
Holds conversation state and all task-execution logic.
"""
import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
from typing import AsyncIterator, Optional

from src.adapters.provider import ModelProvider
from src.mcp import MCPClient
from src.tools import ToolRegistry

logger = logging.getLogger("Nexus")


@dataclass
class LoopDetector:
    """Detect when the agent is stuck in a repetitive tool-call loop."""

    max_history: int = 10
    similar_threshold: int = 5

    _tool_call_history: deque = field(default_factory=deque)
    _output_history: deque = field(default_factory=deque)

    def _hash_args(self, args: dict) -> str:
        if not args:
            return ""
        items = list(args.items())[:3]
        short = {k: str(v)[:50] for k, v in items}
        return str(sorted(short.items()))

    def _hash_output(self, output: str) -> str:
        if not output:
            return ""
        return hashlib.md5(output[:200].encode()).hexdigest()[:8]

    def record_tool_call(self, tool_name: str, args: dict):
        entry = (tool_name, self._hash_args(args))
        self._tool_call_history.append(entry)
        if len(self._tool_call_history) > self.max_history:
            self._tool_call_history.popleft()

    def record_output(self, output: str):
        if output and len(output) > 20:
            entry = self._hash_output(output)
            self._output_history.append(entry)
            if len(self._output_history) > self.max_history:
                self._output_history.popleft()

    def detect_loop(self) -> tuple[bool, str]:
        if len(self._tool_call_history) >= self.similar_threshold:
            recent = list(self._tool_call_history)[-self.similar_threshold:]
            if len(set(recent)) == 1:
                tool_name = recent[0][0]
                return True, f"检测到重复工具调用: 连续 {self.similar_threshold} 次调用相同的 '{tool_name}'"

        if len(self._output_history) >= self.similar_threshold:
            recent = list(self._output_history)[-self.similar_threshold:]
            if len(set(recent)) == 1:
                return True, f"检测到重复输出: 连续 {self.similar_threshold} 次产生相同的输出"

        return False, ""


class AgentSession(ModelProvider):
    """Execution engine: manages conversation state and task execution.

    Owns:
    - model_adapter, tool_registry, mcp_client, tool_orchestrator
    - messages, system_prompt
    - plan_mode, tasks_mode, rounds_since_todo flags

    NexusCLI creates and delegates to this; PlanModeManager / TasksModeManager
    accept AgentSession directly, removing their dependency on the CLI layer.
    """

    def __init__(self, model_adapter, cwd: Optional[str] = None):
        self.model_adapter = model_adapter
        self.cwd: Optional[str] = cwd

        # Tool infrastructure
        self.tool_registry = ToolRegistry()
        from src.team.tools import TeamTool
        team_tool = TeamTool(provider=self)
        self.tool_registry.register(team_tool)

        # Inject provider into already-registered SubagentTool
        subagent_tool = self.tool_registry.get("subagent")
        if subagent_tool:
            subagent_tool._provider = self

        # Register load_skill tool (two-layer skill model)
        from src.tools.skill_tool import LoadSkillTool
        self.tool_registry.register(LoadSkillTool())

        self.mcp_client = MCPClient()
        self.tool_orchestrator = None  # set by caller after construction

        # Conversation state
        self.messages: list[dict] = []
        self.system_prompt: Optional[str] = None

        # Mode flags (checked inside execute_task for Nag Reminder)
        self.plan_mode: bool = False
        self.tasks_mode: bool = False
        self.rounds_since_todo: int = 0
        self._reminder_injected: bool = False  # avoids O(n) reverse scan

        # MCP approval and background tasks
        from src.mcp.approval import MCPToolApproval
        self.tool_approval = MCPToolApproval()
        from src.tools.background import get_background_manager
        self.bg_manager = get_background_manager()

    # ──────────────────────────────────────────────
    # ModelProvider interface
    # ──────────────────────────────────────────────

    def get_adapter(self):
        return self.model_adapter

    def set_adapter(self, adapter):
        self.model_adapter = adapter

    # ──────────────────────────────────────────────
    # Context compression
    # ──────────────────────────────────────────────

    def _compress_context(self, keep_recent: int = 10) -> int:
        if len(self.messages) <= keep_recent:
            return 0
        system_msgs = [m for m in self.messages if m.get("role") == "system"]
        recent_msgs = [m for m in self.messages if m.get("role") != "system"][-keep_recent:]
        removed = len(self.messages) - len(system_msgs) - len(recent_msgs)
        self.messages = system_msgs + recent_msgs
        logger.info(
            f"[compress_context] 压缩上下文：删除了 {removed} 条早期消息，"
            f"保留 system({len(system_msgs)}) + recent({len(recent_msgs)})"
        )
        return removed

    async def _compress_context_llm(self) -> bool:
        if not self.messages:
            return False
        from src.context.core import LLMContextCompressor
        result = await LLMContextCompressor.compress_messages(self.messages, self.model_adapter)
        if result is not None:
            original_non_sys = sum(1 for m in self.messages if m.get("role") != "system")
            self.messages = result
            logger.info(
                f"[compress_context_llm] 压缩完成：{original_non_sys} 条非系统消息 → 1 条摘要"
            )
            return True
        self._compress_context()
        return False

    # ──────────────────────────────────────────────
    # Tool execution
    # ──────────────────────────────────────────────

    async def _execute_tool_call(self, tool_call: dict, iteration: int) -> tuple[dict, str]:
        tool_name = tool_call["name"]
        args = tool_call["arguments"]
        result = None

        try:
            if "__parse_error__" in args:
                raise ValueError(f"工具 {tool_name} 的参数格式错误: {args['__parse_error__']}")

            from src.mcp.client import parse_qualified_tool_name
            from src.mcp.approval import ApprovalDecision
            tool = None
            try:
                server, actual_name = parse_qualified_tool_name(tool_name)
                if self.mcp_client.is_connected(server):
                    decision = await self.tool_approval.check(server, actual_name, args)
                    if decision == ApprovalDecision.DENY:
                        result = "Tool call denied by approval policy"
                    elif decision == ApprovalDecision.PROMPT:
                        result = "Tool call requires user approval (not yet implemented)"
                    else:
                        result = await self.mcp_client.call_tool(server, actual_name, args)
                else:
                    tool = self.tool_registry.get(tool_name)
            except ValueError:
                # Not an MCP tool name
                tool = self.tool_registry.get(tool_name)

            if result is None and tool:
                from src.tools.context import ToolContext
                context = ToolContext(
                    tool_name=tool_name,
                    args=args,
                    cwd=Path(self.cwd) if self.cwd else None,
                    tracker=None,
                    gate=self.tool_orchestrator.gate if self.tool_orchestrator and hasattr(tool, 'is_mutating') and tool.is_mutating else None
                )
                if self.tool_orchestrator:
                    result = await self.tool_orchestrator.execute(tool, args, context)
                else:
                    result = await tool.execute(**args)
            elif result is None:
                result = await self.tool_registry.execute(tool_name, **args)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            result = {"error": str(e)}

        return tool_call, result

    async def _execute_tools_parallel(
        self,
        tool_calls: list[dict],
        iteration: int
    ) -> list[tuple[dict, str]]:
        from src.tools.dependency_analyzer import DependencyAnalyzer

        for idx, tc in enumerate(tool_calls):
            if not tc.get("id"):
                tc["id"] = f"auto_{idx}_{tc.get('name', 'unknown')}"

        analyzer = DependencyAnalyzer()
        batches = analyzer.analyze(tool_calls)
        results = []

        for batch in batches:
            if len(batch) == 1:
                tool_call, result = await self._execute_tool_call(batch[0], iteration)
                results.append((tool_call, result))
            else:
                tasks = [self._execute_tool_call(tc, iteration) for tc in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(batch_results):
                    if isinstance(result, Exception):
                        logger.error(f"并行工具执行异常: {result}")
                        batch_results[i] = (batch[i], {"error": str(result)})
                results.extend(batch_results)

        if len(results) > 1:
            original_order = {tc["id"]: idx for idx, tc in enumerate(tool_calls)}
            results.sort(key=lambda x: original_order.get(x[0].get("id"), 0))
        return results

    async def _execute_task_streaming(
        self,
        tools_schema: list,
        system_prompt: str,
    ):
        from src.adapters.base import StreamEventType, ChatResult
        from src.utils.output import get_output_sink

        logger.info(f"[execute_task_streaming] 开始流式调用，工具数量: {len(tools_schema)}")

        response_parts = []
        tool_calls = []
        stop_reason = None
        sink = get_output_sink()

        sink.start_streaming()

        try:
            async for event in self.model_adapter.chat_stream(
                self.messages,
                tools_schema,
                system_prompt
            ):
                if event.type == StreamEventType.TEXT_DELTA:
                    if event.content:
                        response_parts.append(event.content)
                        sink.print_streaming_text(event.content)
                elif event.type == StreamEventType.TOOL_USE_COMPLETE:
                    if event.tool_calls:
                        tool_calls = event.tool_calls
                elif event.type == StreamEventType.MESSAGE_STOP:
                    stop_reason = event.stop_reason
        except Exception as e:
            logger.error(f"[execute_task_streaming] 流式调用出错: {e}")
            sink.clear_streaming_buffer()
            raise

        response = "".join(response_parts)
        sink.print_streaming_line()
        return ChatResult(text=response, tool_calls=tool_calls, stop_reason=stop_reason)

    async def _confirm_task_completion(self, last_response: str) -> bool:
        confirm_msg = {
            "role": "user",
            "content": "请用一句话确认：你是否已完成了用户交给你的任务？"
                       "如果完成了，回答「任务完成」；如果没完成或不确定，回答「任务未完成」。"
        }
        confirm_messages = self.messages + [confirm_msg]
        try:
            response = await self.model_adapter.chat(confirm_messages, None)
            logger.info(f"[execute_task] 任务完成确认响应: {response[:200]}")
            response_clean = response.strip().replace("**", "").replace("*", "")
            if response_clean == "任务完成":
                return True
            elif response_clean == "任务未完成":
                return False
            else:
                logger.warning(f"[execute_task] 确认响应不明确: {response[:100]}")
                return False
        except Exception as e:
            logger.error(f"[execute_task] 任务完成确认失败: {e}")
            return False

    # ──────────────────────────────────────────────
    # Main execution entry point
    # ──────────────────────────────────────────────

    async def execute_task(self, task: str):
        """Execute a task and yield AgentEvents."""
        # Lazy import to avoid circular: src.agent.__init__ ← session ← src.agent
        from src.agent import AgentEvent, EventType

        # Add user message
        self.messages.append({"role": "user", "content": task})

        # Context compression check
        if len(self.messages) > 2:
            # Tier-2: micro-compact older tool results before threshold check
            from src.context.micro_compactor import micro_compact_messages
            micro_compact_messages(self.messages, keep_recent=3)

            from src.agent.context import AgentContext
            temp_context = AgentContext()
            total_tokens = temp_context.calculate_total_tokens(self.messages)
            if temp_context.should_compress(total_tokens):
                logger.warning(
                    f"[execute_task] 上下文超过70%阈值 ({total_tokens} tokens)，开始压缩"
                )
                yield AgentEvent(EventType.OUTPUT, f"[上下文压缩] 当前使用 {total_tokens} tokens，开始压缩...")
                await self._compress_context_llm()
                yield AgentEvent(EventType.OUTPUT, "[上下文压缩] 完成")

        # Get tool schemas (built-in + MCP)
        tools_schema = list(self.tool_registry.get_tools_schema())
        for server in self.mcp_client.list_servers():
            tools_schema.extend(self.mcp_client.get_tools_schema(server))

        system_prompt = self.system_prompt or ""

        if not tools_schema:
            response = await self.model_adapter.chat(self.messages, system_prompt)
            yield AgentEvent(EventType.OUTPUT, response)
            self.messages.append({"role": "assistant", "content": response})
            return

        yield AgentEvent(EventType.THINKING, "分析任务中...")

        try:
            use_streaming = (
                hasattr(self.model_adapter, 'chat_stream') and
                self.model_adapter.supports_streaming()
            )

            if use_streaming:
                result = await self._execute_task_streaming(tools_schema, system_prompt)
            else:
                result = await self.model_adapter.chat_with_tools_and_stop_reason(
                    self.messages, tools_schema, system_prompt
                )
            response = result.text
            tool_calls = result.tool_calls
            last_stop_reason = result.stop_reason

            if tool_calls:
                logger.debug(f"[execute_task] 工具调用: {[tc['name'] for tc in tool_calls]}")

            loop_detector = LoopDetector()
            max_tool_calls = 100
            tool_call_count = 0
            while tool_calls:
                tool_call_count += 1
                if tool_call_count > max_tool_calls:
                    logger.warning(f"[execute_task] 达到最大工具调用数 ({max_tool_calls})，强制停止")
                    yield AgentEvent(EventType.OUTPUT, "达到最大工具调用数，任务中断")
                    yield AgentEvent(EventType.DONE, "任务中断")
                    return

                for tc in tool_calls:
                    loop_detector.record_tool_call(tc.get("name", "unknown"), tc.get("arguments", {}))

                is_looping, loop_reason = loop_detector.detect_loop()
                if is_looping:
                    logger.warning(f"[execute_task] 检测到循环: {loop_reason}")
                    yield AgentEvent(EventType.OUTPUT, "检测到执行循环，任务中断")
                    yield AgentEvent(EventType.DONE, "任务中断")
                    return

                assistant_message = {
                    "role": "assistant",
                    "content": response or "",
                    "tool_calls": tool_calls,
                }
                self.messages.append(assistant_message)

                tool_results = await self._execute_tools_parallel(tool_calls, tool_call_count)

                for tool_call, result in tool_results:
                    tool_name = tool_call["name"]

                    if tool_name == "todo":
                        self.rounds_since_todo = 0
                        self._reminder_injected = False
                    else:
                        self.rounds_since_todo += 1

                    yield AgentEvent(
                        EventType.TOOL_CALL,
                        f"调用工具: {tool_name}",
                        metadata={"tool_name": tool_name, "args": tool_call.get("arguments", {})}
                    )

                    error_msg = None
                    if isinstance(result, dict) and "error" in result:
                        error_msg = result["error"]
                    elif isinstance(result, str) and result.startswith("Error:"):
                        error_msg = result

                    if error_msg:
                        logger.warning(f"[execute_task] 工具 {tool_name} 执行出错: {error_msg}")
                        yield AgentEvent(
                            EventType.TOOL_RESULT,
                            f"Error: {error_msg}",
                            metadata={"tool_name": tool_name}
                        )
                    else:
                        yield AgentEvent(
                            EventType.TOOL_RESULT,
                            str(result),
                            metadata={"tool_name": tool_name}
                        )

                    # Tier-1: persist large tool outputs to disk, keep preview
                    from src.context.tool_persister import persist_tool_output
                    preview = persist_tool_output(
                        tool_call.get("id", f"tool_{tool_name}"),
                        str(result)
                    )
                    self.messages.append({
                        "role": "tool",
                        "content": preview,
                        "tool_call_id": tool_call.get("id"),
                    })

                # Nag Reminder (skip in plan/tasks mode)
                if self.plan_mode or self.tasks_mode:
                    self.rounds_since_todo = 0
                elif self.rounds_since_todo >= 3 and not self._reminder_injected:
                    self.messages.append({
                        "role": "system",
                        "content": "<reminder>请更新任务列表</reminder>"
                    })
                    self._reminder_injected = True
                    self.rounds_since_todo = 0

                # Drain background notifications before next LLM call
                notifications = self.bg_manager.drain_notifications()
                if notifications:
                    notif_text = "\n".join(
                        f"[bg:{n['task_id']}] {n['status']}: {n['result']}"
                        for n in notifications
                    )
                    self.messages.append({
                        "role": "user",
                        "content": f"<background-results>\n{notif_text}\n</background-results>"
                    })
                    self.messages.append({
                        "role": "assistant",
                        "content": "我注意到以下后台任务已完成：\n" + notif_text
                    })

                try:
                    result = await self.model_adapter.chat_with_tools_and_stop_reason(
                        self.messages, tools_schema, system_prompt
                    )
                    response = result.text
                    tool_calls = result.tool_calls
                    last_stop_reason = result.stop_reason
                    if response:
                        loop_detector.record_output(response)
                except asyncio.CancelledError:
                    logger.warning("[execute_task] 任务执行被取消")
                    yield AgentEvent(EventType.DONE, "任务中断")
                    raise

            # Final response
            if response:
                if not use_streaming:
                    yield AgentEvent(EventType.OUTPUT, response)
                self.messages.append({"role": "assistant", "content": response})

            yield AgentEvent(EventType.DONE, "任务完成")

            # Clean up Nag Reminder messages
            self.messages = [
                m for m in self.messages
                if not (m.get("role") == "system" and "<reminder>" in m.get("content", ""))
            ]

        except asyncio.CancelledError:
            logger.warning("[execute_task] 任务执行被取消")
            raise
        except Exception as e:
            logger.error(f"[execute_task] 执行出错: {str(e)}")
            yield AgentEvent(EventType.ERROR, str(e))


__all__ = ["AgentSession", "LoopDetector"]
