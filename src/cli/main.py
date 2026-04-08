"""CLI interface for Nexus"""
import asyncio
import argparse
import os
import logging
import hashlib
from pathlib import Path
from typing import AsyncIterator, Optional
from dataclasses import dataclass, field
from collections import deque

from src.agent import AgentEvent, EventType, ToolDefinition, ToolResult
from src.config import load_config, save_config, update_provider_config, set_default_provider, get_configured_providers
from src.adapters import create_adapter, set_current_adapter, ModelProvider, AdapterRegistry
from src.tools import ToolRegistry
from src.skills import SkillRegistry
from src.mcp import MCPClient, MCPServerConfig
from src.context import MemoryManager, get_user_memory_dir, NexusMDLoader, AutoMemoryManager
from src.cli.completion import create_input_session, get_input_async as comp_get_input_async
from src.cli.rich_ui import (
    print_init_info,
    print_welcome,
    print_sessions_table,
    print_help,
    print_thinking,
    print_tool_call,
    print_tool_result,
    print_output,
    print_error_output,
    print_warning,
    print_done,
    print_saved,
    input_with_prompt,
    console,
    print_settings_menu,
    print_provider_select,
    print_provider_config_form,
    print_default_provider_select,
    print_api_protocol_select,
    print_plan_mode_indicator,
    print_tasks_mode_indicator,
    start_streaming,
    print_streaming_text,
    print_streaming_line,
    clear_streaming_buffer,
)
from src.adapters.base import StreamEventType

logger = logging.getLogger("Nexus")
import uuid


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


def print_event(event: AgentEvent):
    """Print an event to the console using Rich"""
    if event.type == EventType.THINKING:
        print_thinking(event.content)
    elif event.type == EventType.TOOL_CALL:
        tool_name = event.metadata.get("tool_name", event.content)
        args = event.metadata.get("args")
        print_tool_call(tool_name, args)
    elif event.type == EventType.TOOL_RESULT:
        tool_name = event.metadata.get("tool_name", "tool")
        print_tool_result(tool_name, event.content)
    elif event.type == EventType.OUTPUT:
        print_output(event.content)
    elif event.type == EventType.ERROR:
        print_error_output(event.content)
    elif event.type == EventType.WARNING:
        print_warning(event.content)
    elif event.type == EventType.DONE:
        print_done(event.content)


class NexusCLI(ModelProvider):
    """Main CLI class implementing ModelProvider for dependency injection."""

    def __init__(self, config: dict, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path
        self.model_adapter = None

    # ModelProvider interface implementation
    def get_adapter(self):
        """Get the current model adapter."""
        return self.model_adapter

    def set_adapter(self, adapter):
        """Set the current model adapter."""
        self.model_adapter = adapter
        # Also set global for backward compatibility (Teammate, etc.)
        set_current_adapter(adapter)
        self.tool_registry = ToolRegistry()
        # Inject self as ModelProvider into SubagentTool
        subagent_tool = self.tool_registry.get('subagent')
        if subagent_tool:
            subagent_tool._provider = self
        self.skill_registry = SkillRegistry()
        self.mcp_client = MCPClient()
        # Register TeamTool to this instance's registry
        from src.team.tools import TeamTool
        self.tool_registry.register(TeamTool())
        self.messages = []
        self.system_prompt = None
        # Memory management
        self.memory_manager = MemoryManager()
        self.auto_memory_manager = AutoMemoryManager()
        self.session_id = str(uuid.uuid4())
        self.current_title = "新对话"
        # Input session for command completion
        self._input_session = None
        self._completion_commands = None
        # Skills directory monitoring
        self._skills_last_check = 0
        from src.skills import get_user_skills_dir
        self._user_skills_dir = get_user_skills_dir()
        # Plan mode
        self.plan_mode = False
        from src.cli.plan_mode import PlanModeManager
        self.plan_manager = PlanModeManager(self)
        # Tasks mode
        self.tasks_mode = False
        from src.tasks.tasks_mode import TasksModeManager
        self.tasks_manager = TasksModeManager(self)
        # Tool orchestrator
        self.tool_orchestrator = None
        # MCP tool approval system
        from src.mcp.approval import MCPToolApproval
        self.tool_approval = MCPToolApproval()
        # Background task manager
        from src.tools.background import get_background_manager
        self.bg_manager = get_background_manager()
        # Nag Reminder mechanism - 跟踪自上次 todo 工具调用以来的轮次
        self.rounds_since_todo = 0

    def _create_model_adapter(self, model_config: dict) -> "ModelAdapter":
        """Create model adapter based on config.

        Args:
            model_config: Full models config dict

        Returns:
            Model adapter instance
        """
        default_model = model_config.get("default", "anthropic")

        if default_model in ("minimax",):
            # minimax is a preset of custom with specific settings
            minimax_config = model_config.get("minimax", {})
            return create_adapter(
                "custom",
                base_url=minimax_config.get("base_url", "https://api.minimaxi.com/anthropic"),
                api_key=minimax_config.get("api_key"),
                model=minimax_config.get("model", "MiniMax-M2.7"),
                compat=minimax_config.get("compat"),
                api_protocol="anthropic"
            )

        # All other providers use registry
        return AdapterRegistry.create(default_model, model_config)

    async def initialize(self):
        """Initialize the CLI"""
        self.cwd = str(Path.cwd())
        # Create model adapter
        model_config = self.config.get("models", {})
        self.model_adapter = self._create_model_adapter(model_config)

        # Set current adapter for subagent access
        self.set_adapter(self.model_adapter)

        # Connect MCP servers (non-blocking, background)
        self._mcp_connection_task = asyncio.create_task(self._connect_mcp_servers())

        # 加载 MCP 审批配置
        mcp_config = self.config.get("mcp", {})
        self.tool_approval.load_from_config(mcp_config)

        # 加载所有 skills 元数据并生成 system_prompt
        self._load_skills_prompt()

        # Print initialization info with Rich
        print_init_info(
            provider=model_config.get("default", "anthropic"),
            model=self.model_adapter.get_name(),
            memory_dir=str(self.memory_manager.memory_dir),
            cwd=str(Path.cwd())
        )

        # Initialize completion commands
        self._update_completion_commands()

        # Initialize tool orchestrator
        from src.tools.context import ToolGate
        from src.tools.orchestrator import ToolOrchestrator
        self.tool_orchestrator = ToolOrchestrator(ToolGate())

    def _compress_context(self, keep_recent: int = 10) -> int:
        """Compress conversation history by keeping recent messages.

        Args:
            keep_recent: Number of recent messages to keep

        Returns:
            Number of messages removed
        """
        if len(self.messages) <= keep_recent:
            return 0

        # 保留 system message + 最近的消息（和子 Agent 保持一致）
        system_msgs = [m for m in self.messages if m.get("role") == "system"]
        recent_msgs = [m for m in self.messages if m.get("role") != "system"][-keep_recent:]

        removed = len(self.messages) - len(system_msgs) - len(recent_msgs)
        self.messages = system_msgs + recent_msgs
        logger.info(f"[compress_context] 压缩上下文：删除了 {removed} 条早期消息，保留 system({len(system_msgs)}) + recent({len(recent_msgs)})")
        return removed

    async def _compress_context_llm(self) -> bool:
        """使用 LLM 智能压缩上下文。

        将所有非 system 消息交给 LLM 提炼精简信息，
        然后用总结替代所有非 system 消息。

        Returns:
            True if compression succeeded
        """
        if not self.messages:
            return False

        # 分离 system 和非 system 消息
        system_msgs = [m for m in self.messages if m.get("role") == "system"]
        non_system_msgs = [m for m in self.messages if m.get("role") != "system"]

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
            response = await self.model_adapter.chat(
                [{"role": "user", "content": summarize_prompt}],
                ""
            )

            if not response:
                logger.warning("[compress_context_llm] LLM 摘要返回为空，回退到简单压缩")
                self._compress_context()
                return False

            # 用摘要替换非 system 消息
            summary_msg = {"role": "system", "content": f"[对话摘要]\n{response}"}
            self.messages = system_msgs + [summary_msg]

            original_tokens = sum(len(m.get("content", "")) for m in non_system_msgs) // 4
            summary_tokens = len(response) // 4
            logger.info(f"[compress_context_llm] 压缩完成：原始约 {original_tokens} tokens → 摘要 {summary_tokens} tokens")
            return True

        except Exception as e:
            logger.error(f"[compress_context_llm] 压缩失败: {e}，回退到简单压缩")
            self._compress_context()
            return False

    def _load_tools_prompt(self) -> str:
        """生成可用工具的提示词，区分内置工具和 MCP 工具"""
        lines = []

        # 内置工具
        builtin_tools = self.tool_registry.list_tools()
        if builtin_tools:
            lines.append("<builtin_tools>")
            lines.append("以下是你可用的内置工具（本地执行）：")
            for tool_name in builtin_tools:
                tool = self.tool_registry.get(tool_name)
                if tool:
                    lines.append(f"  - {tool.name}: {tool.description}")
            lines.append("</builtin_tools>")

        # MCP 工具 - 从配置读取，不依赖连接状态
        mcp_config = self.config.get("mcp", {}).get("servers") or []
        if mcp_config:
            lines.append("\n<mcp_tools>")
            lines.append("以下是你可通过 MCP (Model Context Protocol) 连接的外部工具：")
            lines.append(f"\nMCP 配置文件位置: {Path(self.config_path).absolute()}")
            lines.append("如需添加或修改 MCP 服务器，请编辑上述配置文件中的 mcp.servers 部分。")

            # 已连接的服务器
            connected_servers = self.mcp_client.list_servers()
            if connected_servers:
                lines.append("\n  [已连接的服务器]")
                for server in connected_servers:
                    tools = self.mcp_client.get_tools_schema(server)
                    if tools:
                        lines.append(f"\n  [{server}] 服务器提供以下工具：")
                        for tool in tools:
                            tool_name = tool.get("name", "").replace(f"{server}_", "")
                            description = tool.get("description", "")
                            lines.append(f"    - {tool_name}: {description}")

            # 配置但未连接的服务器
            configured_not_connected = [
                s for s in mcp_config
                if s.get("enabled", True) and s.get("name") not in connected_servers
            ]
            if configured_not_connected:
                lines.append("\n  [配置但未连接的服务器]")
                for server in configured_not_connected:
                    server_name = server.get("name", "unknown")
                    server_type = server.get("type", "stdio")
                    lines.append(f"\n  [{server_name}] 类型: {server_type}")
                    if server_type == "http":
                        lines.append(f"    URL: {server.get('url', 'N/A')}")
                    else:
                        lines.append(f"    命令: {' '.join(server.get('command', []))}")

            lines.append("</mcp_tools>")

        return "\n".join(lines) if lines else ""

    def _load_skills_prompt(self) -> None:
        """加载所有 skills 元数据并生成 system_prompt"""
        from src.skills import load_all_skills_metadata, get_user_skills_dir

        skills_metadata = load_all_skills_metadata()
        skills_prompt = self._build_skills_prompt(skills_metadata)

        # 获取用户 skills 目录
        user_skills_dir = get_user_skills_dir()

        # 添加工具信息
        tools_prompt = self._load_tools_prompt()

        # 添加 skills 目录信息到 system prompt
        skills_dir_info = f"""
## 用户技能目录
用户自定义技能存储在: {user_skills_dir}
如需重新加载技能，请使用 /reload 命令"""

        # 添加时间信息到 system prompt
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        time_info = f"""
## 当前时间
当前系统时间: {current_time}
请注意：回答涉及时间的问题时，应以该时间为准。"""

        # 添加工作区信息到 system prompt
        workspace_info = f"""
## 当前工作区
当前工作目录 (workspace): {self.cwd}
所有文件操作默认在此目录下进行，除非明确指定其他路径。"""

        # 添加命令列表到 system prompt
        commands_info = self._build_commands_prompt()

        # 加载 NEXUS.md 内容（项目知识）
        nexus_content = NexusMDLoader.load_and_merge(Path(self.cwd))
        nexus_section = ""
        if nexus_content:
            nexus_section = f"""
## 项目知识 (NEXUS.md)
{nexus_content}
"""

        # 加载近期记忆
        memories_section = ""
        if hasattr(self, 'auto_memory_manager'):
            memories_section = self.auto_memory_manager.get_memories_section(limit=5)
            if memories_section:
                memories_section = f"\n{memories_section}\n"

        # 合并到 system_prompt
        base_prompt = self.config.get("system_prompt", "You are Nexus, a helpful AI assistant.")
        parts = [base_prompt, time_info, workspace_info, commands_info,
                 skills_dir_info, tools_prompt, nexus_section, memories_section, skills_prompt]
        self.system_prompt = "\n\n".join([p for p in parts if p])

    def _build_commands_prompt(self) -> str:
        """生成可用命令列表的提示词"""
        from src.commands import get_command_registry

        lines = []
        lines.append("\n## 可用命令")
        lines.append("用户可以通过以下命令与系统交互：")

        registry = get_command_registry()
        commands = registry.get_all()

        if commands:
            for cmd in commands:
                name = cmd.name
                desc = cmd.description if hasattr(cmd, 'description') else "无描述"
                aliases = cmd.aliases if hasattr(cmd, 'aliases') and cmd.aliases else []
                alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
                lines.append(f"  /{name}{alias_str}: {desc}")

        lines.append("\n提示：输入 /help 可以查看更详细的帮助信息。")
        return "\n".join(lines)

    def _check_and_reload_skills(self) -> bool:
        """检查 skills 目录是否有变化，如有则重新加载"""
        import time
        import os

        if not self._user_skills_dir.exists():
            return False

        # 获取目录最新修改时间
        latest_mtime = 0
        for root, dirs, files in os.walk(self._user_skills_dir):
            for f in files:
                if f == "SKILL.md":
                    fpath = os.path.join(root, f)
                    mtime = os.path.getmtime(fpath)
                    if mtime > latest_mtime:
                        latest_mtime = mtime

        # 首次调用（值为0），只记录不加载
        if self._skills_last_check == 0 and latest_mtime > 0:
            self._skills_last_check = latest_mtime
            return False

        # 如果有变化，重新加载
        if latest_mtime > self._skills_last_check:
            self._skills_last_check = latest_mtime
            self._reload_skills()
            return True
        return False

    def _reload_skills(self) -> None:
        """重新加载所有 skills"""
        from src.skills import SkillLoader

        # 重新加载 skills
        loader = SkillLoader()
        skills = loader.load_all()

        # 重新注册到 registry
        from src.skills import Skill
        self.skill_registry = SkillRegistry()

        # 重新加载 skill handlers
        for skill in skills:
            if skill.handler:
                skill_obj = Skill(
                    name=skill.name,
                    description=skill.description,
                    aliases=skill.aliases,
                    handler=skill.handler,
                    requires_args=skill.requires_args
                )
                self.skill_registry.register(skill_obj)

        # 重新生成 system_prompt
        self._load_skills_prompt()

        # 更新补全命令
        self._update_completion_commands()

    def enter_plan_mode(self) -> None:
        """Enter plan mode"""
        self.plan_mode = True
        self.plan_manager.enter()

    def exit_plan_mode(self) -> None:
        """Exit plan mode"""
        self.plan_mode = False
        self.plan_manager.exit()

    def enter_tasks_mode(self) -> None:
        """Enter tasks mode"""
        self.tasks_mode = True
        self.tasks_manager.enter()

    def exit_tasks_mode(self) -> None:
        """Exit tasks mode"""
        self.tasks_mode = False
        self.tasks_manager.exit()

    def _build_skills_prompt(self, skills_metadata: list) -> str:
        """生成可用 skills 的提示词"""
        if not skills_metadata:
            return ""

        lines = ["<available_skills>"]
        for skill in skills_metadata:
            location = str(skill.file_path) if skill.file_path else "built-in"
            lines.append(f"""  <skill>
    <name>{skill.name}</name>
    <description>{skill.description}</description>
    <location>{location}</location>
  </skill>""")
        lines.append("</available_skills>")
        return "\n".join(lines)

    async def execute_task(self, task: str) -> AsyncIterator[AgentEvent]:
        """Execute a task and yield events"""
        # Check for slash command first
        parsed = self.skill_registry.parse_input(task)
        if parsed:
            skill_name, args, _ = parsed
            skill = self.skill_registry.get(skill_name)
            if skill:
                context = {"cwd": ".", "messages": self.messages}
                async for event in skill.handler(args, context):
                    yield event
                return

        # Regular task - use agent with tools
        system_prompt = self.system_prompt

        # Add user message first, then check compression with NEW total
        self.messages.append({"role": "user", "content": task})

        # Then check compression after adding user message
        if len(self.messages) > 2:
            from src.agent.context import AgentContext
            temp_context = AgentContext()
            total_tokens = temp_context.calculate_total_tokens(self.messages)
            if temp_context.should_compress(total_tokens):
                logger.warning(f"[execute_task] 上下文超过70%阈值 ({total_tokens} tokens)，开始压缩")
                yield AgentEvent(EventType.OUTPUT, f"[上下文压缩] 当前使用 {total_tokens} tokens，开始压缩...")
                await self._compress_context_llm()
                yield AgentEvent(EventType.OUTPUT, "[上下文压缩] 完成")

        # Update title if this is the first user message
        if self.current_title == "新对话" and task:
            self.current_title = task[:50] + ("..." if len(task) > 50 else "")

        # Get tool schemas
        tools_schema = self.tool_registry.get_tools_schema()

        # Add MCP tools if available
        for server in self.mcp_client.list_servers():
            tools_schema.extend(self.mcp_client.get_tools_schema(server))

        if not tools_schema:
            # Simple chat without tools
            response = await self.model_adapter.chat(self.messages, system_prompt)
            yield AgentEvent(EventType.OUTPUT, response)
            self.messages.append({"role": "assistant", "content": response})
            return

        # Check for completed background tasks before LLM call
        # Drain to clear old notifications so they won't be processed again
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

        # Chat with tools
        yield AgentEvent(EventType.THINKING, "分析任务中...")

        try:
            # Check if streaming is supported and enabled
            use_streaming = (
                hasattr(self.model_adapter, 'chat_stream') and
                self.model_adapter.supports_streaming()
            )

            if use_streaming:
                # Use streaming for first call
                result = await self._execute_task_streaming(
                    tools_schema, system_prompt
                )
                response = result.text
                tool_calls = result.tool_calls
                last_stop_reason = result.stop_reason
            else:
                # Fall back to non-streaming
                result = await self.model_adapter.chat_with_tools_and_stop_reason(
                    self.messages,
                    tools_schema,
                    system_prompt
                )
                response = result.text
                tool_calls = result.tool_calls
                last_stop_reason = result.stop_reason
            if tool_calls:
                logger.debug(f"[execute_task] 工具调用: {[tc['name'] for tc in tool_calls]}")

            # Process tool calls with loop detection
            loop_detector = LoopDetector()
            max_tool_calls = 100  # 硬性限制，防止无限循环
            tool_call_count = 0

            while tool_calls:
                tool_call_count += 1

                # 检查是否超过最大工具调用数
                if tool_call_count > max_tool_calls:
                    logger.warning(f"[execute_task] 达到最大工具调用数 ({max_tool_calls})，强制停止")
                    yield AgentEvent(EventType.OUTPUT, f"达到最大工具调用数，任务中断")
                    yield AgentEvent(EventType.DONE, "任务中断")
                    return

                # 检查循环
                for tc in tool_calls:
                    loop_detector.record_tool_call(tc.get("name", "unknown"), tc.get("arguments", {}))

                is_looping, loop_reason = loop_detector.detect_loop()
                if is_looping:
                    logger.warning(f"[execute_task] 检测到循环: {loop_reason}")
                    # 只记录日志，不在 UI 上显示
                    yield AgentEvent(EventType.OUTPUT, f"检测到执行循环，任务中断")
                    yield AgentEvent(EventType.DONE, "任务中断")
                    return

                # Build assistant message with tool calls for message history
                assistant_message = {
                    "role": "assistant",
                    "content": response or "",
                    "tool_calls": tool_calls
                }
                self.messages.append(assistant_message)

                # Execute tools with parallel execution where possible
                tool_results = await self._execute_tools_parallel(tool_calls, tool_call_count)

                # Process results and add to messages
                for tool_call, result in tool_results:
                    tool_name = tool_call["name"]

                    # Nag Reminder tracking - 跟踪非 todo 工具调用次数
                    if tool_name == "todo":
                        self.rounds_since_todo = 0
                    else:
                        self.rounds_since_todo += 1

                    # Emit tool call event
                    yield AgentEvent(
                        EventType.TOOL_CALL,
                        f"调用工具: {tool_name}",
                        metadata={"tool_name": tool_name, "args": tool_call.get("arguments", {})}
                    )

                    # Check if result contains error (support both dict and string formats)
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

                    # Add tool result to messages with tool_call_id for proper association
                    self.messages.append({
                        "role": "tool",
                        "content": str(result),
                        "tool_call_id": tool_call.get("id")
                    })

                # Nag Reminder injection - 当非 todo 工具调用达到阈值时注入提醒
                # 但如果处于 plan/tasks mode，跳过 reminder（这些模式自己管理任务列表）
                if self.plan_mode or self.tasks_mode:
                    # Plan/Tasks 模式不需要 Nag Reminder，重置计数器避免误导
                    self.rounds_since_todo = 0
                elif self.rounds_since_todo >= 3:
                    # 检查最近一条 system 消息是否已经是 reminder，避免重复污染
                    last_reminder_msg = None
                    for msg in reversed(self.messages):
                        if msg.get("role") == "system" and "<reminder>" in msg.get("content", ""):
                            last_reminder_msg = msg.get("content", "")
                            break
                    if not last_reminder_msg:
                        self.messages.append({
                            "role": "system",
                            "content": "<reminder>请更新任务列表</reminder>"
                        })
                    self.rounds_since_todo = 0  # 重置计数器

                # Get next response
                # Check for completed background tasks before next LLM call
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
                        self.messages,
                        tools_schema,
                        system_prompt
                    )
                    response = result.text
                    tool_calls = result.tool_calls
                    last_stop_reason = result.stop_reason
                    # 记录输出用于循环检测
                    if response:
                        loop_detector.record_output(response)
                except asyncio.CancelledError:
                    logger.warning("[execute_task] 任务执行被取消")
                    yield AgentEvent(EventType.DONE, "任务中断")
                    raise

            # Final response
            if response:
                yield AgentEvent(EventType.OUTPUT, response)
                self.messages.append({"role": "assistant", "content": response})

            # Check if task was actually completed when model stopped (regardless of stop_reason)
            if not tool_calls:
                # Model stopped and no tool_calls - verify if task was actually completed
                logger.info(f"[execute_task] 模型停止 (stop_reason={last_stop_reason})，发送确认请求")
                task_completed = await self._confirm_task_completion(response)
                if not task_completed:
                    logger.warning(f"[execute_task] 任务可能未完成：stop_reason={last_stop_reason}")
                    yield AgentEvent(
                        EventType.WARNING,
                        f"任务可能未完成：stop_reason={last_stop_reason}"
                    )

            yield AgentEvent(EventType.DONE, "任务完成")

            # 清理 Nag Reminder 消息
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

    async def _confirm_task_completion(self, last_response: str) -> bool:
        """发送确认请求给模型，判断任务是否真正完成。

        @param last_response 模型最后的回复内容
        @return True 如果模型确认完成，False 如果模型说未完成
        """
        confirm_msg = {
            "role": "user",
            "content": "请用一句话确认：你是否已完成了用户交给你的任务？"
                       "如果完成了，回答「任务完成」；如果没完成或不确定，回答「任务未完成」。"
        }
        # 使用 messages 的副本避免污染原始对话历史
        confirm_messages = self.messages + [confirm_msg]
        try:
            response = await self.model_adapter.chat(confirm_messages, None)
            logger.info(f"[execute_task] 任务完成确认响应: {response[:200]}")

            # 使用更精确的匹配（去除 ** 标记）
            response_clean = response.strip().replace("**", "").replace("*", "")
            if response_clean == "任务完成":
                return True
            elif response_clean == "任务未完成":
                return False
            else:
                # 模糊情况，记录日志并假设未完成
                logger.warning(f"[execute_task] 确认响应不明确: {response[:100]}")
                return False
        except Exception as e:
            logger.error(f"[execute_task] 任务完成确认失败: {e}")
            return False  # 确认失败时假设未完成，避免假阳性

    def _update_completion_commands(self) -> None:
        """Update command completion list"""
        builtin_commands = [
            "/help",
            "/exit",
            "/quit",
            "/clear",
            "/sessions",
            "/restore",
            "/models",
            "/settings",
            "/reload",
            "/mcpstatus",
            "/plan",
        ]
        skill_commands = [f"/{name}" for name in self.skill_registry.list_skills()]
        self._completion_commands = builtin_commands + skill_commands

        self._input_session = create_input_session(self._completion_commands)

    def _get_input(self, prompt_str: str) -> str:
        """Get input with command completion"""
        if self._input_session:
            try:
                return comp_get_input(prompt_str, self._input_session)
            except Exception:
                # Fallback to basic input
                return input(prompt_str)
        return input(prompt_str)

    async def _get_input_async(self, prompt_str: str) -> str:
        """Get input with command completion (async)"""
        if self._input_session:
            try:
                # Use simple prompt
                return await comp_get_input_async("> ", self._input_session)
            except Exception:
                # Fallback to basic input
                return input(prompt_str)
        return input(prompt_str)

    async def _connect_mcp_servers(self):
        """后台连接 MCP 服务器，不阻塞主流程"""
        mcp_config = self.config.get("mcp", {}).get("servers") or []
        for server_config in mcp_config:
            try:
                server_type = server_config.get("type", "stdio")

                if server_type == "http":
                    config = MCPServerConfig(
                        name=server_config["name"],
                        type="http",
                        url=server_config.get("url"),
                        headers=server_config.get("headers", {}),
                        enabled=server_config.get("enabled", True)
                    )
                else:
                    config = MCPServerConfig(
                        name=server_config["name"],
                        type="stdio",
                        command=server_config.get("command"),
                        enabled=server_config.get("enabled", True),
                        env=server_config.get("env", {})
                    )
                await self.mcp_client.connect(config)
            except Exception as e:
                logger.warning(f"MCP: 后台连接服务器 {server_config.get('name', 'unknown')} 失败: {e}")

    async def _execute_tool_call(
        self,
        tool_call: dict,
        iteration: int
    ) -> tuple[dict, str]:
        """Execute a single tool call and return the result.

        Args:
            tool_call: Tool call dict with name, arguments, id
            iteration: Current iteration number for logging

        Returns:
            Tuple of (tool_call, result)
        """
        tool_name = tool_call["name"]
        args = tool_call["arguments"]

        result = None

        try:
            # Check for parse errors from adapter
            if "__parse_error__" in args:
                raise ValueError(f"工具 {tool_name} 的参数格式错误: {args['__parse_error__']}")

            # Get tool definition for orchestrator
            tool = None

            # Check if it's an MCP tool (mcp__{server}__{tool} format)
            from src.mcp.client import parse_qualified_tool_name
            from src.mcp.approval import ApprovalDecision
            try:
                server, actual_name = parse_qualified_tool_name(tool_name)
                if self.mcp_client.is_connected(server):
                    # Check approval before executing
                    decision = await self.tool_approval.check(server, actual_name, args)
                    if decision == ApprovalDecision.DENY:
                        result = "Tool call denied by approval policy"
                    elif decision == ApprovalDecision.PROMPT:
                        result = "Tool call requires user approval (not yet implemented)"
                    else:
                        # APPROVE - execute the tool
                        result = await self.mcp_client.call_tool(server, actual_name, args)
            except ValueError:
                # Not an MCP tool, check built-in tools
                tool = self.tool_registry.get(tool_name)

            # Use orchestrator for built-in tools
            if result is None and tool:
                # Built-in tool execution
                from src.tools.context import ToolContext

                context = ToolContext(
                    tool_name=tool_name,
                    args=args,
                    cwd=Path(self.cwd),
                    tracker=None,
                    gate=self.tool_orchestrator.gate if hasattr(tool, 'is_mutating') and tool.is_mutating else None
                )

                result = await self.tool_orchestrator.execute(tool, args, context)
            elif result is None:
                # Fallback to registry execute for non-MCP tools
                result = await self.tool_registry.execute(tool_name, **args)

        except asyncio.CancelledError:
            raise  # 重新抛出取消异常，不包装
        except Exception as e:
            result = {"error": str(e)}  # 统一返回字典格式

        return tool_call, result

    async def _execute_tools_parallel(
        self,
        tool_calls: list[dict],
        iteration: int
    ) -> list[tuple[dict, str]]:
        """Execute multiple tool calls with parallel execution where possible.

        Uses DependencyAnalyzer to determine which tools can run in parallel.
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
                tool_call, result = await self._execute_tool_call(batch[0], iteration)
                results.append((tool_call, result))
            else:
                # Multiple tools - execute in parallel
                tasks = [
                    self._execute_tool_call(tc, iteration)
                    for tc in batch
                ]
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

    async def _execute_task_streaming(
        self,
        tools_schema: list,
        system_prompt: str
    ) -> "ChatResult":
        """Execute task with streaming response.

        This method handles the streaming response from the model,
        displaying text in real-time while collecting tool calls.

        Returns ChatResult with text, tool_calls, and stop_reason.
        """
        from src.adapters.base import StreamEventType, ChatResult

        logger.info(f"[execute_task_streaming] 开始流式调用，工具数量: {len(tools_schema)}")

        response_parts = []
        tool_calls = []
        stop_reason = None

        # Start streaming
        start_streaming()

        try:
            async for event in self.model_adapter.chat_stream(
                self.messages,
                tools_schema,
                system_prompt
            ):
                if event.type == StreamEventType.TEXT_DELTA:
                    # Real-time text output
                    if event.content:
                        response_parts.append(event.content)
                        print_streaming_text(event.content)

                elif event.type == StreamEventType.TOOL_USE_COMPLETE:
                    # Tool calls from the response
                    if event.tool_calls:
                        tool_calls = event.tool_calls
                        logger.info(f"[execute_task_streaming] 收到工具调用: {[tc['name'] for tc in tool_calls]}")

                elif event.type == StreamEventType.MESSAGE_STOP:
                    # End of message - extract stop_reason
                    stop_reason = event.stop_reason
                    logger.info(f"[execute_task_streaming] 流式响应完成, stop_reason={stop_reason}")

        except Exception as e:
            logger.error(f"[execute_task_streaming] 流式调用出错: {e}")
            clear_streaming_buffer()
            raise

        # Finalize response
        response = "".join(response_parts)
        print_streaming_line()  # New line after streaming

        logger.info(f"[execute_task_streaming] 完成 | response长度={len(response)} | tool_calls数量={len(tool_calls)} | stop_reason={stop_reason}")

        return ChatResult(text=response, tool_calls=tool_calls, stop_reason=stop_reason)

    async def chat(self):
        """Run interactive chat"""
        print_welcome()

        # 启动时加载一次 skills
        self._reload_skills()

        while True:
            try:
                prompt = "> "
                if self.plan_mode:
                    print_plan_mode_indicator()
                    prompt = "📋 "

                try:
                    user_input = (await self._get_input_async(prompt)).strip()
                except asyncio.CancelledError:
                    if self.plan_mode:
                        self.exit_plan_mode()
                        console.print("\n[yellow]已退出计划模式[/yellow]")
                        continue
                    if self.messages:
                        self.memory_manager.save_session(
                            self.session_id,
                            self.messages,
                            self.current_title
                        )
                        # Auto Memory: let LLM decide what to remember
                        if len(self.messages) > 4:
                            try:
                                count = await self.auto_memory_manager.process_session(
                                    self.messages, self.session_id, self.model_adapter
                                )
                                if count > 0:
                                    logger.info(f"Auto Memory: saved {count} memories")
                            except Exception as e:
                                logger.warning(f"Auto Memory: failed: {e}")
                        console.print(f"\n[dim]会话已保存:[/dim] {self.memory_manager.memory_dir / f'{self.session_id}.md'}")
                    console.print("\n[cyan]再见![/cyan]")
                    break

                if not user_input:
                    if self.plan_mode:
                        self.exit_plan_mode()
                        console.print("[yellow]已退出计划模式[/yellow]")
                    continue

                if self.plan_mode:
                    try:
                        await self.plan_manager.run(user_input)
                    except asyncio.CancelledError:
                        console.print("\n[yellow]计划执行已取消[/yellow]")
                    self.exit_plan_mode()
                    continue

                if self.tasks_mode:
                    print_tasks_mode_indicator()
                    try:
                        await self.tasks_manager.run(user_input)
                    except asyncio.CancelledError:
                        console.print("\n[yellow]Tasks 执行已取消[/yellow]")
                    self.exit_tasks_mode()
                    console.print("[cyan]Tasks 模式已退出[/cyan]")
                    continue

                self._check_and_reload_skills()

                if user_input.lower() in ["/exit", "/quit", "exit"]:
                    if self.messages:
                        self.memory_manager.save_session(
                            self.session_id,
                            self.messages,
                            self.current_title
                        )
                        # Auto Memory
                        if len(self.messages) > 4:
                            try:
                                count = await self.auto_memory_manager.process_session(
                                    self.messages, self.session_id, self.model_adapter
                                )
                                if count > 0:
                                    logger.info(f"Auto Memory: saved {count} memories")
                            except Exception as e:
                                logger.warning(f"Auto Memory: failed: {e}")
                        print_saved(str(self.memory_manager.memory_dir / f'{self.session_id}.md'))
                    console.print("[cyan]再见![/cyan]")
                    break

                if user_input.startswith("/"):
                    from src.commands import get_command_registry, CommandContext
                    registry = get_command_registry()
                    cmd_name, cmd, args = registry.parse_input(user_input)

                    if cmd:
                        context = CommandContext(
                            args=args,
                            cli=self,
                            session_id=self.session_id,
                            session={"messages": self.messages}
                        )
                        try:
                            async for result in cmd.execute(context):
                                if hasattr(result, 'type') and hasattr(result.type, 'value'):
                                    if result.type.value == "output":
                                        print_output(result.content)
                                    elif result.type.value == "thinking":
                                        print_thinking(result.content)
                                    elif result.type.value == "error":
                                        print_error_output(result.content)
                                    elif result.type.value == "warning":
                                        console.print(f"[yellow]警告: {result.content}[/yellow]")
                                    elif result.type.value == "success":
                                        console.print(f"[green]{result.content}[/green]")
                        except Exception as e:
                            print_error_output(f"命令执行失败: {str(e)}")
                        continue
                    else:
                        console.print(f"[red]未知命令: {cmd_name}[/red]")
                        continue

                if user_input == "/clear":
                    if self.messages:
                        self.memory_manager.save_session(
                            self.session_id,
                            self.messages,
                            self.current_title
                        )
                        # Auto Memory
                        if len(self.messages) > 4:
                            try:
                                count = await self.auto_memory_manager.process_session(
                                    self.messages, self.session_id, self.model_adapter
                                )
                                if count > 0:
                                    logger.info(f"Auto Memory: saved {count} memories")
                            except Exception as e:
                                logger.warning(f"Auto Memory: failed: {e}")
                        console.print("[dim]会话已保存[/dim]")
                    self.messages = []
                    self.session_id = str(uuid.uuid4())
                    self.current_title = "新对话"
                    console.print("[green]已开启新对话[/green]")
                    continue

                async for event in self.execute_task(user_input):
                    print_event(event)

            except KeyboardInterrupt:
                if self.plan_mode:
                    self.exit_plan_mode()
                    console.print("\n[yellow]已退出计划模式[/yellow]")
                    continue
                if self.messages:
                    self.memory_manager.save_session(
                        self.session_id,
                        self.messages,
                        self.current_title
                    )
                    # Auto Memory
                    if len(self.messages) > 4:
                        try:
                            count = await self.auto_memory_manager.process_session(
                                self.messages, self.session_id, self.model_adapter
                            )
                            if count > 0:
                                logger.info(f"Auto Memory: saved {count} memories")
                        except Exception as e:
                            logger.warning(f"Auto Memory: failed: {e}")
                    console.print(f"\n[dim]会话已保存:[/dim] {self.memory_manager.memory_dir / f'{self.session_id}.md'}")
                console.print("\n[cyan]再见![/cyan]")
                break
            except Exception as e:
                print_error_output(str(e))

    async def run_single(self, task: str):
        """Run a single task"""
        async for event in self.execute_task(task):
            print_event(event)

    async def close(self):
        """Cleanup resources"""
        # Cancel background MCP connection task
        if hasattr(self, '_mcp_connection_task') and self._mcp_connection_task:
            self._mcp_connection_task.cancel()
            try:
                await self._mcp_connection_task
            except asyncio.CancelledError:
                pass

        # Save session before closing
        if self.messages:
            self.memory_manager.save_session(
                self.session_id,
                self.messages,
                self.current_title
            )
            # Auto Memory
            if len(self.messages) > 4 and hasattr(self, 'auto_memory_manager'):
                try:
                    count = await self.auto_memory_manager.process_session(
                        self.messages, self.session_id, self.model_adapter
                    )
                    if count > 0:
                        logger.info(f"Auto Memory: saved {count} memories")
                except Exception as e:
                    logger.warning(f"Auto Memory: failed: {e}")
        await self.mcp_client.disconnect_all()

    def _list_sessions(self) -> None:
        """List all saved sessions"""
        sessions = self.memory_manager.list_sessions()
        print_sessions_table(sessions, show_id=True)

    def list_sessions(self) -> list:
        """List all saved sessions (returns list for command use)"""
        return self.memory_manager.list_sessions()

    def restore_session(self, idx: int) -> bool:
        """Restore a session by index (for command use)"""
        sessions = self.memory_manager.list_sessions()
        if 0 <= idx < len(sessions):
            session = sessions[idx]
            messages = self.memory_manager.load_session(session.session_id)
            if messages:
                self.messages = messages
                self.session_id = session.session_id
                self.current_title = session.title
                return True
        return False

    async def _show_mcp_status(self) -> None:
        """显示MCP服务器连接状态（先尝试连接所有配置的服务器）"""
        mcp_config = self.config.get("mcp", {}).get("servers") or []
        if not mcp_config:
            console.print("[yellow]未配置MCP服务器[/yellow]")
            return

        console.print("[cyan]正在连接MCP服务器...[/cyan]")
        for server in mcp_config:
            server_name = server.get("name", "unknown")
            server_type = server.get("type", "stdio")
            enabled = server.get("enabled", True)

            if not enabled:
                continue

            if self.mcp_client.is_connected(server_name):
                continue

            try:
                if server_type == "http":
                    config = MCPServerConfig(
                        name=server_name,
                        type="http",
                        url=server.get("url"),
                        headers=server.get("headers", {}),
                        enabled=True
                    )
                else:
                    config = MCPServerConfig(
                        name=server_name,
                        type="stdio",
                        command=server.get("command"),
                        enabled=True,
                        env=server.get("env", {})
                    )
                if await self.mcp_client.connect(config):
                    console.print(f"  [green]✓[/green] {server_name} 连接成功")
                else:
                    console.print(f"  [red]✗[/red] {server_name} 连接失败")
            except Exception as e:
                console.print(f"  [red]✗[/red] {server_name} 连接异常: {e}")

        console.print("\n[cyan]MCP服务器状态：[/cyan]")
        for server in mcp_config:
            server_name = server.get("name", "unknown")
            server_type = server.get("type", "stdio")
            enabled = server.get("enabled", True)

            if not enabled:
                status_text = "[dim]disabled[/dim]"
            elif self.mcp_client.is_connected(server_name):
                status_text = "[green]connected[/green]"
            else:
                status_text = "[red]disconnected[/red]"

            console.print(f"  {server_name} ({server_type}) - {status_text}")

    def _handle_restore(self) -> None:
        """Handle /restore command"""
        sessions = self.memory_manager.list_sessions()

        if not sessions:
            console.print("[yellow]暂无保存的会话[/yellow]")
            return

        # Show sessions table
        print_sessions_table(sessions, show_id=False)

        console.print("\n[cyan]输入会话编号恢复，或输入 'c' 取消[/cyan]")

        try:
            choice = input_with_prompt("> ").strip()

            if choice.lower() == 'c':
                console.print("[dim]已取消[/dim]")
                return

            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                session = sessions[idx]
                messages = self.memory_manager.load_session(session.session_id)

                if messages:
                    self.messages = messages
                    self.session_id = session.session_id
                    self.current_title = session.title
                    console.print(f"\n[green]已恢复会话: {session.title}[/green]")
                    console.print(f"[dim]共 {len(messages)} 条消息[/dim]")
                else:
                    console.print("[red]加载会话失败[/red]")
            else:
                console.print("[red]无效的编号[/red]")
        except ValueError:
            console.print("[red]请输入有效的编号[/red]")

    def _handle_settings(self) -> None:
        """
        @brief 处理 /settings 命令
        @details 显示设置菜单并根据用户选择执行相应操作
        """
        print_settings_menu()
        console.print("\n[cyan]输入选项编号，或输入 'c' 取消[/cyan]")

        try:
            choice = input_with_prompt("> ").strip()

            if choice.lower() == 'c':
                console.print("[dim]已取消[/dim]")
                return

            if choice == "1":
                self._update_provider_info()
            elif choice == "2":
                self._change_default_provider()
            else:
                console.print("[red]无效的选项[/red]")
        except Exception as e:
            console.print(f"[red]操作失败: {str(e)}[/red]")

    def _update_provider_info(self) -> None:
        """
        @brief 更新供应商信息
        @details 显示供应商选择列表，获取用户配置并保存
        """
        print_provider_select()
        console.print("\n[cyan]输入选项编号，或输入 'c' 取消[/cyan]")

        try:
            choice = input_with_prompt("> ").strip()

            if choice.lower() == 'c':
                console.print("[dim]已取消[/dim]")
                return

            provider_map = {
                "1": "anthropic",
                "2": "openai",
                "3": "ollama",
                "4": "lmstudio",
                "5": "custom",
                "6": "minimax",
                "7": "xai",
            }

            if choice not in provider_map:
                console.print("[red]无效的选项[/red]")
                return

            provider = provider_map[choice]
            print_provider_config_form(provider)

            settings = {}
            provider_lower = provider.lower()
            current_config = self.config.get("models", {}).get(provider, {})

            if provider_lower == "anthropic":
                current_model = current_config.get("model", "")
                console.print("[yellow]API Key 通过环境变量 ANTHROPIC_API_KEY 配置，请自行设置[/yellow]")
                model = input_with_prompt(f"Model [{current_model}]: ").strip()
                if model:
                    settings["model"] = model

            elif provider_lower == "openai":
                current_model = current_config.get("model", "")
                console.print("[yellow]API Key 通过环境变量 OPENAI_API_KEY 配置，请自行设置[/yellow]")
                model = input_with_prompt(f"Model [{current_model}]: ").strip()
                if model:
                    settings["model"] = model

            elif provider_lower == "ollama":
                current_url = current_config.get("url", "")
                current_model = current_config.get("model", "")
                url = input_with_prompt(f"URL [{current_url}]: ").strip()
                model = input_with_prompt(f"Model [{current_model}]: ").strip()
                if url:
                    settings["url"] = url
                if model:
                    settings["model"] = model

            elif provider_lower == "lmstudio":
                current_url = current_config.get("url", "")
                current_model = current_config.get("model", "")
                url = input_with_prompt(f"URL [{current_url}]: ").strip()
                model = input_with_prompt(f"Model [{current_model}]: ").strip()
                if url:
                    settings["url"] = url
                if model:
                    settings["model"] = model

            elif provider_lower == "custom":
                # 先选择 API 协议
                print_api_protocol_select()
                console.print("\n[cyan]输入选项编号选择 API 协议（直接回车保持不变）[/cyan]")
                protocol_choice = input_with_prompt("> ").strip()

                # 只在用户明确选择时更新 api_protocol
                if protocol_choice == "1":
                    settings["api_protocol"] = "openai"
                elif protocol_choice == "2":
                    settings["api_protocol"] = "anthropic"
                # 如果直接回车，保持原配置不变

                # 然后输入其他配置，显示当前值
                current_base_url = current_config.get("base_url", "")
                current_model = current_config.get("model", "")
                console.print("[yellow]API Key 通过环境变量 CUSTOM_API_KEY 配置，请自行设置[/yellow]")
                base_url = input_with_prompt(f"Base URL [{current_base_url}]: ").strip()
                model = input_with_prompt(f"Model [{current_model}]: ").strip()

                if base_url:
                    settings["base_url"] = base_url
                if model:
                    settings["model"] = model

            elif provider_lower == "minimax":
                current_model = current_config.get("model", "")
                console.print("[yellow]API Key 通过环境变量 MINIMAX_API_KEY 配置，请自行设置[/yellow]")
                model = input_with_prompt(f"Model [{current_model}]: ").strip()
                if model:
                    settings["model"] = model
                # MiniMax 使用 Anthropic API 兼容格式
                settings["api_protocol"] = "anthropic"
                settings["base_url"] = "https://api.minimaxi.com/anthropic"

            elif provider_lower == "xai":
                current_model = current_config.get("model", "")
                console.print("[yellow]API Key 通过环境变量 XAI_API_KEY 配置，请自行设置[/yellow]")
                model = input_with_prompt(f"Model [{current_model}]: ").strip()
                if model:
                    settings["model"] = model

            if not settings:
                console.print("[yellow]未输入任何配置，已取消[/yellow]")
                return

            self.config = update_provider_config(self.config, provider, settings)
            if save_config(self.config, self.config_path):
                console.print(f"[green]已更新 {provider} 配置[/green]")
            else:
                console.print("[red]保存配置失败[/red]")
                return

            self._change_default_provider()

        except Exception as e:
            console.print(f"[red]配置失败: {str(e)}[/red]")

    def _change_default_provider(self) -> None:
        """
        @brief 更换默认供应商
        @details 显示已配置的供应商列表，让用户选择默认供应商
        """
        providers = get_configured_providers(self.config)

        if not providers:
            console.print("[yellow]暂无已配置的供应商，请先配置供应商[/yellow]")
            return

        current_default = self.config.get("models", {}).get("default", "")
        print_default_provider_select(providers, current_default)
        console.print("\n[cyan]输入选项编号，或输入 'c' 取消[/cyan]")

        try:
            choice = input_with_prompt("> ").strip()

            if choice.lower() == 'c':
                console.print("[dim]已取消[/dim]")
                return

            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                selected_provider = providers[idx]
                self.config = set_default_provider(self.config, selected_provider)
                if save_config(self.config, self.config_path):
                    console.print(f"[green]已将默认供应商设置为: {selected_provider}[/green]")
                    self._reinit_model_adapter()
                else:
                    console.print("[red]保存配置失败[/red]")
            else:
                console.print("[red]无效的编号[/red]")
        except ValueError:
            console.print("[red]请输入有效的编号[/red]")
        except Exception as e:
            console.print(f"[red]操作失败: {str(e)}[/red]")

    def _reinit_model_adapter(self) -> None:
        """
        @brief 重新初始化模型适配器
        @details 根据当前配置重新创建模型适配器
        """
        model_config = self.config.get("models", {})
        self.model_adapter = self._create_model_adapter(model_config)

        # Update current adapter for subagent access
        self.set_adapter(self.model_adapter)

        console.print(f"[green]已切换到模型: {self.model_adapter.get_name()}[/green]")


async def main():
    """Main entry point"""
    import sys

    parser = argparse.ArgumentParser(description="Nexus - Personal AI Agent")
    parser.add_argument("task", nargs="?", help="Task to execute")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--model", choices=["anthropic", "openai", "ollama", "lmstudio", "custom", "minimax", "xai"], help="Model to use")

    args = parser.parse_args()

    # Determine config path: use exe directory if running as frozen exe
    if args.config:
        config_path = args.config
    elif getattr(sys, 'frozen', False):
        # Running as PyInstaller frozen exe - use exe directory
        config_path = Path(sys.executable).parent / "config.yaml"
        config_path = str(config_path)
    else:
        config_path = "config.yaml"

    # Load config with env var substitution
    config = load_config(config_path)

    # Override model if specified
    if args.model:
        config.setdefault("models", {})["default"] = args.model

    # Create and run CLI
    cli = NexusCLI(config, args.config)

    # Create a task that can be cancelled
    async def run_cli():
        await cli.initialize()
        if args.task:
            await cli.run_single(args.task)
        else:
            await cli.chat()

    current_task = asyncio.current_task()
    if current_task:
        # Store task reference for cancellation
        cli._main_task = current_task

    try:
        await run_cli()
    except asyncio.CancelledError:
        # Task was cancelled (e.g., by Ctrl+C)
        logger.warning("[main] 任务被取消")
        console.print("\n[yellow]任务已中断[/yellow]")
    finally:
        # Try to close gracefully, but ignore errors during shutdown
        try:
            if cli.memory_manager and cli.messages:
                cli.memory_manager.save_session(
                    cli.session_id,
                    cli.messages,
                    cli.current_title
                )
        except Exception:
            pass

        try:
            await cli.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def cancel_current_task():
        current_task = asyncio.current_task()
        if current_task:
            current_task.cancel()

    def handle_interrupt():
        print("\n[yellow]正在中断任务...[/yellow]", flush=True)
        cancel_current_task()

    # Set up signal handler for Unix-like systems
    if sys.platform != 'win32':
        import signal
        loop.add_signal_handler(signal.SIGINT, handle_interrupt)

    try:
        try:
            loop.run_until_complete(main())
        except KeyboardInterrupt:
            # Windows: KeyboardInterrupt is raised when Ctrl+C is pressed
            handle_interrupt()
            # Wait for the task to be cancelled
            try:
                loop.run_until_complete(asyncio.sleep(0.1))
            except asyncio.CancelledError:
                pass
    except asyncio.CancelledError:
        pass  # Task was cancelled as expected
    finally:
        loop.close()