"""CLI interface for Nexus"""
import asyncio
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

from src.agent import AgentEvent, EventType
from src.adapters import ModelProvider, AdapterRegistry
from src.mcp import MCPServerConfig
from src.context import MemoryManager, AutoMemoryManager
from src.cli.rich_ui import (
    print_init_info,
    print_thinking,
    print_tool_call,
    print_tool_result,
    print_output,
    print_error_output,
    print_warning,
    print_done,
)

logger = logging.getLogger("Nexus")


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
    concerns here: session persistence, settings, TUI entry.
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
        """使用 SystemPromptBuilder 构建系统提示词（流水线架构）"""
        from src.context import SystemPromptBuilder

        self.system_prompt_builder = SystemPromptBuilder(
            config=self.config,
            cwd=self._session.cwd if self._session else str(Path.cwd()),
            tool_registry=self._session.tool_registry if self._session else None,
            memory_manager=getattr(self, 'auto_memory_manager', None),
            mcp_client=getattr(self, 'mcp_client', None),
            mcp_config=self.config.get("mcp", {}).get("servers", []),
            config_path=self.config_path,
        )

        self.system_prompt = self.system_prompt_builder.build_full()

    def _rebuild_system_prompt(self) -> None:
        """重建系统提示词（用于配置变更或缓存失效后）"""
        if hasattr(self, 'system_prompt_builder') and self.system_prompt_builder:
            self.system_prompt_builder.invalidate_cache()
            self.system_prompt = self.system_prompt_builder.build_full()

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
        if hasattr(self, 'system_prompt_builder') and self.system_prompt_builder:
            self._rebuild_system_prompt()
        else:
            self._load_skills_prompt()

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

        All execution is delegated to AgentSession.
        """
        # Update title from first user message (session metadata, stays in CLI)
        if self.current_title == "新对话" and task:
            self.current_title = task[:50] + ("..." if len(task) > 50 else "")

        # Delegate all execution to AgentSession
        async for event in self._session.execute_task(task):
            yield event

    async def _connect_mcp_servers(self):
        """后台连接 MCP 服务器，不阻塞主流程"""
        if not self._session or not hasattr(self._session, 'mcp_router'):
            logger.warning("[MCP] Session 或 mcp_router 未初始化，跳过 MCP 连接")
            return

        from src.mcp.plugin import PluginLoader

        plugin_loader = PluginLoader()
        plugin_loader.scan()
        plugin_configs = plugin_loader.get_mcp_server_configs()

        mcp_config = self.config.get("mcp", {}).get("servers") or []
        mcp_router = self._session.mcp_router
        all_configs = []

        for server_config in mcp_config:
            config = self._build_mcp_config(server_config)
            if config:
                all_configs.append(config)
            else:
                logger.warning(f"MCP: 配置解析失败 {server_config.get('name', 'unknown')}")

        for qualified_name, server_config in plugin_configs.items():
            plugin_cfg = dict(server_config)
            plugin_cfg["name"] = qualified_name
            config = self._build_mcp_config(plugin_cfg)
            if config:
                all_configs.append(config)
            else:
                logger.warning(f"MCP: 插件配置解析失败 {qualified_name}")

        await mcp_router.connect_servers(all_configs)
        mcp_router.register_tools_to_registry(self._session.tool_registry)
        await mcp_router.start_health_check()

        logger.info(f"[MCP] MCP 子系统初始化完成，已连接 {len(mcp_router.mcp_client.list_servers())} 个服务器")

    async def run_single(self, task: str):
        """Run a single task"""
        async for event in self.execute_task(task):
            print_event(event)

    async def run_tui(self):
        """Run the Textual full-screen TUI interface (default UI)."""
        from src.cli.tui.app import NexusApp

        self._reload_skills()
        app = NexusApp(self, config_path=self.config_path)
        await app.run_async()

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

    def _build_mcp_config(self, server_config: dict) -> Optional[MCPServerConfig]:
        """从配置字典构建 MCPServerConfig

        Args:
            server_config: 服务器配置字典

        Returns:
            MCPServerConfig 或 None（如果配置无效）
        """
        server_name = server_config.get("name")
        if not server_name:
            return None

        server_type = server_config.get("type", "stdio")
        if server_type == "http":
            return MCPServerConfig(
                name=server_name,
                type="http",
                url=server_config.get("url"),
                headers=server_config.get("headers", {}),
                enabled=server_config.get("enabled", True)
            )
        else:
            return MCPServerConfig(
                name=server_name,
                type="stdio",
                command=server_config.get("command"),
                enabled=server_config.get("enabled", True),
                env=server_config.get("env", {})
            )
