"""CLI interface for Nexus"""
import asyncio
import argparse
import os
import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from src.agent import AgentEvent, EventType, ToolDefinition, ToolResult
from src.config import load_config, save_config, update_provider_config, set_default_provider, get_configured_providers
from src.adapters import ModelProvider, AdapterRegistry
from src.tools import ToolRegistry
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
)

logger = logging.getLogger("Nexus")
import uuid


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
    """Main CLI class implementing ModelProvider for dependency injection.

    Delegates all execution logic to AgentSession, keeping only UI/session
    concerns here: input loop, slash commands, session persistence, settings.
    """

    def __init__(self, config: dict, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path
        self._session = None  # AgentSession — set by set_adapter()

    # ModelProvider interface — delegates to session

    def get_adapter(self):
        return self._session.model_adapter if self._session else None

    def set_adapter(self, adapter):
        """(Re)create AgentSession with the given adapter.

        Called during initialize() and on model switch.
        """
        from src.agent.session import AgentSession

        prev_cwd = self._session.cwd if self._session else None
        self._session = AgentSession(adapter, cwd=prev_cwd)

        # Give the session its own tool orchestrator so it can execute tools
        from src.tools.context import ToolGate
        from src.tools.orchestrator import ToolOrchestrator
        self._session.tool_orchestrator = ToolOrchestrator(ToolGate())

        # Inject self as ModelProvider into SubagentTool so subagents can
        # resolve the adapter through the same provider chain as NexusCLI.
        subagent_tool = self._session.tool_registry.get('subagent')
        if subagent_tool:
            subagent_tool._provider = self._session

        # UI / session state
        self.memory_manager = MemoryManager()
        self.auto_memory_manager = AutoMemoryManager()
        self.session_id = str(uuid.uuid4())
        self.current_title = "新对话"

        # Input / completion
        self._input_session = None
        self._completion_commands = None

        # Skills directory monitoring state
        self._skills_last_mtime = 0.0
        self._skills_last_file_count = 0

        # Mode managers depend on AgentSession, not NexusCLI — keeps execution logic out of CLI layer
        from src.cli.plan_mode import PlanModeManager
        self.plan_manager = PlanModeManager(self._session)
        from src.tasks.tasks_mode import TasksModeManager
        self.tasks_manager = TasksModeManager(self._session)

    # Properties delegating to AgentSession

    @property
    def model_adapter(self):
        return self._session.model_adapter if self._session else None

    @model_adapter.setter
    def model_adapter(self, value):
        if self._session:
            self._session.model_adapter = value

    @property
    def messages(self):
        return self._session.messages if self._session else []

    @messages.setter
    def messages(self, value):
        if self._session:
            self._session.messages = value

    @property
    def system_prompt(self):
        return self._session.system_prompt if self._session else None

    @system_prompt.setter
    def system_prompt(self, value):
        if self._session:
            self._session.system_prompt = value

    @property
    def plan_mode(self):
        return self._session.plan_mode if self._session else False

    @plan_mode.setter
    def plan_mode(self, value):
        if self._session:
            self._session.plan_mode = value

    @property
    def tasks_mode(self):
        return self._session.tasks_mode if self._session else False

    @tasks_mode.setter
    def tasks_mode(self, value):
        if self._session:
            self._session.tasks_mode = value

    @property
    def tool_registry(self):
        return self._session.tool_registry if self._session else None

    @property
    def mcp_client(self):
        return self._session.mcp_client if self._session else None

    @property
    def tool_orchestrator(self):
        return self._session.tool_orchestrator if self._session else None

    @tool_orchestrator.setter
    def tool_orchestrator(self, value):
        if self._session:
            self._session.tool_orchestrator = value

    @property
    def tool_approval(self):
        return self._session.tool_approval if self._session else None

    @property
    def bg_manager(self):
        return self._session.bg_manager if self._session else None

    def _create_model_adapter(self, model_config: dict) -> "ModelAdapter":
        """Create model adapter based on config.

        Args:
            model_config: Full models config dict

        Returns:
            Model adapter instance
        """
        default_model = model_config.get("default", "anthropic")
        return AdapterRegistry.create(default_model, model_config)

    async def initialize(self):
        """Initialize the CLI"""
        from src.utils.output import RichOutputSink, set_output_sink
        set_output_sink(RichOutputSink())
        model_config = self.config.get("models", {})
        adapter = self._create_model_adapter(model_config)
        self.set_adapter(adapter)  # creates self._session with tool orchestrator
        self._session.cwd = str(Path.cwd())

        self._mcp_connection_task = asyncio.create_task(self._connect_mcp_servers())

        mcp_config = self.config.get("mcp", {})
        self.tool_approval.load_from_config(mcp_config)

        self._load_skills_prompt()

        print_init_info(
            provider=model_config.get("default", "anthropic"),
            model=self.model_adapter.get_name(),
            memory_dir=str(self.memory_manager.memory_dir),
            cwd=str(Path.cwd())
        )

        # Initialize completion commands
        self._update_completion_commands()

    async def _process_auto_memory(self) -> int:
        """Process auto memory after session end. Returns count of memories saved."""
        if len(self.messages) <= 4:
            return 0
        try:
            count = await self.auto_memory_manager.process_session(
                self.messages, self.session_id, self.model_adapter
            )

            # Trigger consolidation if memories were saved
            if count > 0:
                self.auto_memory_manager.trigger_consolidation(self.model_adapter)

            return count
        except Exception as e:
            logger.warning(f"Auto Memory: failed: {e}")
            return 0

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
        """Load skills and generate system_prompt using two-layer model"""
        from src.skills import get_skill_catalog, get_user_skills_dir

        catalog = get_skill_catalog()

        # 获取用户 skills 目录
        user_skills_dir = get_user_skills_dir()

        # Layer 1: cheap catalog for system prompt
        skills_catalog = catalog.describe_available()
        skills_dir_info = f"""
## Skills
用户自定义技能存储在: {user_skills_dir}
如需使用某项技能，请通过 load_skill 工具加载其完整内容。
可用技能：
{skills_catalog}"""

        # 添加工具信息
        tools_prompt = self._load_tools_prompt()

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
当前工作目录 (workspace): {self._session.cwd}
所有文件操作默认在此目录下进行，除非明确指定其他路径。"""

        # 添加命令列表到 system prompt
        commands_info = self._build_commands_prompt()

        # 添加 hook 配置信息
        hooks_info = self._build_hooks_prompt()

        # 加载 NEXUS.md 内容（项目知识）
        nexus_content = NexusMDLoader.load_and_merge(Path(self._session.cwd))
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

        # 加载记忆指导
        guidance_section = ""
        if hasattr(self, 'auto_memory_manager'):
            guidance = self.auto_memory_manager.get_guidance()
            if guidance:
                guidance_section = f"\n## 记忆指导\n{guidance}\n"

        # 合并到 system_prompt
        base_prompt = self.config.get("system_prompt", "You are Nexus, a helpful AI assistant.")
        parts = [base_prompt, time_info, workspace_info, commands_info,
                 skills_dir_info, tools_prompt, nexus_section, memories_section, guidance_section, hooks_info]
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

    def _build_hooks_prompt(self) -> str:
        """生成 Hook 系统说明的提示词"""
        hooks_path = Path.home() / ".nexus" / "hooks.json"
        trust_marker = Path.home() / ".nexus" / "trusted"

        return f"""
## Hook 系统 (实验性功能)

Nexus 支持 Hook 机制，允许你在特定事件发生时执行自定义脚本。

### Hook 配置文件
位置: {hooks_path}

### 支持的事件
| 事件 | 触发时机 |
|------|----------|
| agent_start | Agent 会话开始时 |
| agent_end | Agent 会话结束时 |
| iteration_start | 每次迭代开始时 |
| iteration_end | 每次迭代结束时 |
| tool_call_start | 工具执行前 |
| tool_call_end | 工具执行后 |
| tool_blocked | 工具被阻止时 |
| context_compressed | 上下文压缩时 |
| session_start | 用户会话开始时 |
| session_end | 用户会话结束时 |

### 配置格式
```json
{{
  "hooks": {{
    "tool_call_start": [
      {{
        "id": "bash_guard",
        "matcher": "bash",
        "command": "/path/to/check.sh"
      }}
    ]
  }},
  "trust_all": false
}}
```

### 字段说明
- `matcher`: 工具名过滤器，"*" 表示所有工具
- `command`: 要执行的命令（支持 shell 脚本）
- `id`: Hook 的唯一标识符（可选）

### 退出码契约
- `0`: 继续执行
- `1`: 阻止操作
- `2`: 注入消息到上下文

### Hook 环境变量
执行时提供以下环境变量：
- `HOOK_EVENT`: 事件名称
- `HOOK_TOOL_NAME`: 工具名称
- `HOOK_TOOL_INPUT`: 工具输入参数 (JSON)
- `HOOK_TOOL_OUTPUT`: 工具输出结果
- `HOOK_ITERATION`: 当前迭代次数
- `HOOK_AGENT_ID`: Agent 标识符

### 使用示例
用户可以让 Agent 帮你配置 hook，例如：
- "帮我配置一个 bash 工具的 hook，在执行前检查命令是否安全"
- "添加一个 iteration_start hook，每次迭代开始时打印日志"
- "配置一个 tool_call_end hook，记录所有工具执行结果"

**安全说明**: Hook 仅在受信任的工作区执行。如需启用，请创建 {trust_marker} 文件。
"""

    def _check_and_reload_skills(self) -> bool:
        """检查 skills 目录是否有变化，如有则重新加载（两层模型）"""
        from src.skills import get_user_skills_dir

        user_skills_dir = get_user_skills_dir()
        if not user_skills_dir.exists():
            return False

        # 获取所有 SKILL.md 文件的最新修改时间和数量
        latest_mtime = 0
        file_count = 0
        for f in user_skills_dir.rglob("SKILL.md"):
            file_count += 1
            mtime = f.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime

        # 获取上次状态
        prev_mtime = getattr(self, '_skills_last_mtime', 0)
        prev_count = getattr(self, '_skills_last_file_count', 0)

        # 首次调用（prev_mtime == 0 且目录不为空），只记录不加载
        if prev_mtime == 0 and latest_mtime > 0:
            self._skills_last_mtime = latest_mtime
            self._skills_last_file_count = file_count
            return False

        # 文件数量变化（新增/删除）或最新 mtime 上升时触发重载
        if file_count != prev_count or latest_mtime > prev_mtime:
            self._skills_last_mtime = latest_mtime
            self._skills_last_file_count = file_count
            self._reload_skills()
            return True
        return False

    def _reload_skills(self) -> None:
        """重新加载所有 skills（两层模型：只需清除缓存）"""
        from src.skills import get_skill_catalog

        get_skill_catalog().invalidate_cache()
        self._load_skills_prompt()
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

    async def execute_task(self, task: str) -> AsyncIterator[AgentEvent]:
        """Execute a task and yield events.

        Skill commands are handled here (CLI layer); all other execution
        is delegated to AgentSession.
        """
        # Update title from first user message (session metadata, stays in CLI)
        if self.current_title == "新对话" and task:
            self.current_title = task[:50] + ("..." if len(task) > 50 else "")

        # Delegate all execution to AgentSession
        async for event in self._session.execute_task(task):
            yield event

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
        self._completion_commands = builtin_commands

        self._input_session = create_input_session(self._completion_commands)

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

    async def chat(self):
        """Run interactive chat"""
        print_welcome()
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
                        # Auto Memory
                        count = await self._process_auto_memory()
                        if count > 0:
                            logger.info(f"Auto Memory: saved {count} memories")
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
                        count = await self._process_auto_memory()
                        if count > 0:
                            logger.info(f"Auto Memory: saved {count} memories")
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
                        count = await self._process_auto_memory()
                        if count > 0:
                            logger.info(f"Auto Memory: saved {count} memories")
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
                    count = await self._process_auto_memory()
                    if count > 0:
                        logger.info(f"Auto Memory: saved {count} memories")
                    console.print(f"\n[dim]会话已保存:[/dim] {self.memory_manager.memory_dir / f'{self.session_id}.md'}")
                console.print("\n[cyan]再见![/cyan]")
                break
            except asyncio.CancelledError:
                if self.plan_mode:
                    self.exit_plan_mode()
                    console.print("\n[yellow]已退出计划模式[/yellow]")
                else:
                    console.print("\n[yellow]任务已中断[/yellow]")
                break
            except RuntimeError as e:
                # When CancelledError escapes an async generator, Python raises
                # RuntimeError: cannot reuse already awaited coroutine. Re-raise as
                # CancelledError so it propagates correctly.
                if "cannot reuse already awaited" in str(e):
                    if self.plan_mode:
                        self.exit_plan_mode()
                        console.print("\n[yellow]已退出计划模式[/yellow]")
                    else:
                        console.print("\n[yellow]任务已中断[/yellow]")
                    break
                print_error_output(str(e))
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
            count = await self._process_auto_memory()
            if count > 0:
                logger.info(f"Auto Memory: saved {count} memories")
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
        """Handle /settings command — show settings menu and process user choices."""
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
        """Update provider info — show provider list, get config, save."""
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
        """Change default provider — show configured providers, let user select default."""
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
        """Reinitialize the model adapter from current config."""
        model_config = self.config.get("models", {})
        new_adapter = self._create_model_adapter(model_config)
        self.set_adapter(new_adapter)  # recreates AgentSession with fresh state

        console.print(f"[green]已切换到模型: {self.model_adapter.get_name()}[/green]")


async def main():
    """Main entry point"""
    import sys

    parser = argparse.ArgumentParser(description="Nexus - Personal AI Agent")
    parser.add_argument("task", nargs="?", help="Task to execute")
    parser.add_argument("--config", default=None, help="Config file path")
    parser.add_argument("--model", choices=AdapterRegistry.list_providers(), help="Model to use")

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
    cli = NexusCLI(config, config_path)

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