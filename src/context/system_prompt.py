"""System Prompt Builder - Pipeline architecture for dynamic system prompt construction."""
import datetime
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry
    from src.mcp import MCPClient

logger = logging.getLogger("Nexus")

# Separator between static and dynamic sections
DYNAMIC_BOUNDARY = "\n\n=== DYNAMIC_BOUNDARY ===\n"

# Default limits
DEFAULT_MEMORY_LIMIT = 5
MEMORY_BODY_PREVIEW_MAX = 300

# Tool categories for organized display
TOOL_CATEGORIES = {
    "文件操作": ["file_read", "file_write", "file_patch", "file_search", "list_dir"],
    "Shell": ["bash", "shell"],
    "代码执行": ["code_exec"],
    "任务管理": ["todo", "tasks"],
    "子代理": ["subagent", "check_subagent"],
    "后台任务": ["background_run", "check_background"],
    "技能": ["load_skill"],
    "团队协作": ["team"],
}


@dataclass
class SystemPromptConfig:
    """Configuration for system prompt sections."""
    include_base_prompt: bool = True
    include_nexus_md: bool = True
    include_skills: bool = True
    include_memory: bool = True
    include_memory_guidance: bool = True
    include_tools: bool = True
    include_tools_categorized: bool = True
    include_commands: bool = True
    include_hooks: bool = True
    memory_limit: int = 5


def build_system_reminder(task_context: str = "", extra: str = None) -> str:
    """
    Build a system-reminder user message for per-turn dynamic content.

    This is injected as a user message, not as system prompt, keeping
    per-turn dynamic content separate from the stable system instructions.
    """
    parts = []
    if task_context:
        parts.append(f"当前任务: {task_context}")
    if extra:
        parts.append(extra)

    if not parts:
        return ""

    content = "<system-reminder>\n" + "\n".join(parts) + "\n</system-reminder>"
    return content


class SystemPromptBuilder:
    """
    Pipeline architecture for building system prompts.

    STATIC SECTIONS (built once, cached):
    - Base prompt, NEXUS.md chain, Skill catalog, Memory entries,
      Memory guidance, Tool listings, Commands, Hooks

    DYNAMIC SECTIONS (rebuilt per turn):
    - Current time, Current workspace/cwd
    """

    def __init__(
        self,
        config: dict,
        cwd: str,
        tool_registry: "ToolRegistry" = None,
        memory_manager=None,
        mcp_client: "MCPClient" = None,
        mcp_config: list = None,
        config_path: str = None,
    ):
        self.config = config
        self.cwd = cwd
        self.tool_registry = tool_registry
        self.memory_manager = memory_manager
        self.mcp_client = mcp_client
        self.mcp_config = mcp_config or []
        self._config_path = config_path or "config.yaml"

        self._section_config = SystemPromptConfig()
        self._load_section_config()

        # Caches
        self._static_cache: Optional[str] = None

    # --- Section Builders ---

    def _build_base_prompt(self) -> str:
        """Build base prompt from config."""
        if not self._section_config.include_base_prompt:
            return ""

        base = self.config.get(
            "system_prompt",
            "You are Nexus, a helpful AI assistant."
        )
        # Handle both string and dict with 'instructions' key
        if isinstance(base, dict):
            base = base.get("instructions", "You are Nexus, a helpful AI assistant.")
        return base

    def _build_nexus_md_section(self) -> str:
        """Build NEXUS.md chain section (global + project + subdir)."""
        if not self._section_config.include_nexus_md:
            return ""

        from .nexus_md import NexusMDLoader

        lines = []
        nexus_contents = NexusMDLoader.load_and_merge(Path(self.cwd))

        if nexus_contents:
            lines.append("## 项目知识 (NEXUS.md)")
            lines.append(nexus_contents)

        return "\n".join(lines)

    def _build_skills_section(self) -> str:
        """Build skill catalog section."""
        if not self._section_config.include_skills:
            return ""

        from src.skills import get_skill_catalog, get_user_skills_dir

        catalog = get_skill_catalog()
        skills_catalog = catalog.describe_available()
        user_skills_dir = get_user_skills_dir()

        lines = ["## Skills", f"用户自定义技能存储在: {user_skills_dir}"]
        lines.append("如需使用某项技能，请通过 load_skill 工具加载其完整内容。")
        lines.append(f"\n可用技能：\n{skills_catalog}")

        return "\n".join(lines)

    def _build_memory_section(self) -> str:
        """Build memory section with body content (enhanced)."""
        if not self._section_config.include_memory:
            return ""

        if not self.memory_manager:
            return ""

        limit = self._section_config.memory_limit
        entries = self.memory_manager.get_recent_memories(limit)

        if not entries:
            return ""

        from .auto_memory import MEMORY_TYPE_EMOJI

        lines = ["## 长期记忆", ""]

        for entry in entries:
            emoji = MEMORY_TYPE_EMOJI.get(entry.memory_type, "📝")
            scope_marker = "📍" if entry.memory_scope == "session" else ""

            lines.append(f"**{emoji} {entry.memory_type}**{scope_marker}: {entry.summary}")

            # Include body content preview
            if entry.content and entry.content.strip():
                body = entry.content.strip()
                if len(body) > MEMORY_BODY_PREVIEW_MAX:
                    body = body[:MEMORY_BODY_PREVIEW_MAX] + "..."
                lines.append(f"    {body}")

            # Include tags if present
            if entry.tags:
                lines.append(f"    标签: {', '.join(entry.tags)}")

        return "\n".join(lines)

    def _build_memory_guidance_section(self) -> str:
        """Build memory guidance section."""
        if not self._section_config.include_memory_guidance:
            return ""

        if not self.memory_manager:
            return ""

        guidance = self.memory_manager.get_guidance()
        if guidance:
            return f"## 记忆指导\n{guidance}"

        return ""

    def _build_tools_section(self) -> str:
        """Build tool listings section with optional categorization."""
        if not self._section_config.include_tools:
            return ""

        lines = ["<builtin_tools>", "以下是你可用的内置工具（本地执行）："]

        if self._section_config.include_tools_categorized and self.tool_registry:
            # Categorized display
            categorized = self._get_tools_by_category()
            for category, tools in categorized.items():
                lines.append(f"\n  [{category}]")
                for tool_name in tools:
                    tool = self.tool_registry.get(tool_name)
                    if tool:
                        params = self._get_tool_params(tool)
                        lines.append(f"    - {tool.name}({params}): {tool.description}")
        else:
            # Simple list
            if self.tool_registry:
                for tool_name in self.tool_registry.list_tools():
                    tool = self.tool_registry.get(tool_name)
                    if tool:
                        params = self._get_tool_params(tool)
                        lines.append(f"  - {tool.name}({params}): {tool.description}")

        lines.append("</builtin_tools>")

        # MCP tools section
        mcp_section = self._build_mcp_tools_section()
        if mcp_section:
            lines.append(mcp_section)

        return "\n".join(lines)

    def _get_tool_params(self, tool) -> str:
        """Extract parameter names from tool schema."""
        try:
            schema = tool.get_schema()
            props = schema.get("input_schema", {}).get("properties", {})
            if props:
                return ", ".join(props.keys())
            return ""
        except Exception:
            return ""

    def _get_tools_by_category(self) -> dict[str, list[str]]:
        """Get tools grouped by category."""
        if not self.tool_registry:
            return {}

        # Build set of all categorized tool names for O(1) lookup
        categorized_names = set()
        for tool_names in TOOL_CATEGORIES.values():
            categorized_names.update(tool_names)

        result = {}
        for category, tool_names in TOOL_CATEGORIES.items():
            tools_in_category = [
                name for name in tool_names
                if self.tool_registry.get(name)
            ]
            if tools_in_category:
                result[category] = tools_in_category

        # Add uncategorized tools
        uncategorized = [
            name for name in self.tool_registry.list_tools()
            if name not in categorized_names
        ]
        if uncategorized:
            result["其他"] = uncategorized

        return result

    def _build_mcp_tools_section(self) -> str:
        """Build MCP tools section from config."""
        if not self.mcp_config:
            return ""

        lines = ["\n<mcp_tools>", "以下是你可通过 MCP (Model Context Protocol) 连接的外部工具："]

        # Config location hint
        lines.append(f"\nMCP 配置文件位置: {self._config_path}")
        lines.append("如需添加或修改 MCP 服务器，请编辑上述配置文件中的 mcp.servers 部分。")

        # Connected servers
        if self.mcp_client:
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

        # Configured but not connected
        connected_names = set(self.mcp_client.list_servers()) if self.mcp_client else set()
        configured_not_connected = [
            s for s in self.mcp_config
            if s.get("enabled", True) and s.get("name") not in connected_names
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

        return "\n".join(lines)

    def _build_commands_section(self) -> str:
        """Build commands section."""
        if not self._section_config.include_commands:
            return ""

        from src.commands import get_command_registry

        lines = ["\n## 可用命令", "用户可以通过以下命令与系统交互："]

        registry = get_command_registry()
        commands = registry.get_all()

        if commands:
            for cmd in commands:
                name = cmd.name
                desc = getattr(cmd, 'description', "无描述")
                aliases = getattr(cmd, 'aliases', []) or []
                alias_str = f" (别名: {', '.join(aliases)})" if aliases else ""
                lines.append(f"  /{name}{alias_str}: {desc}")

        lines.append("\n提示：输入 /help 可以查看更详细的帮助信息。")
        return "\n".join(lines)

    def _build_hooks_section(self) -> str:
        """Build hooks system documentation section."""
        if not self._section_config.include_hooks:
            return ""

        nexus_home = Path.home() / ".nexus"
        hooks_path = nexus_home / "hooks.json"
        trust_marker = nexus_home / "trusted"

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

    def _build_dynamic_section(self) -> str:
        """Build dynamic context section (time, cwd)."""
        lines = ["# Dynamic context", ""]

        # Current time
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        lines.append(f"当前系统时间: {current_time}")

        # Current workspace
        lines.append(f"当前工作目录 (workspace): {self.cwd}")
        lines.append("所有文件操作默认在此目录下进行，除非明确指定其他路径。")

        return "\n".join(lines)

    # --- Public API ---

    def build_static(self) -> str:
        """Build static sections with caching."""
        if self._static_cache is not None:
            return self._static_cache

        sections = []

        # Build each static section
        builders = [
            self._build_base_prompt,
            self._build_nexus_md_section,
            self._build_skills_section,
            self._build_memory_section,
            self._build_memory_guidance_section,
            self._build_tools_section,
            self._build_commands_section,
            self._build_hooks_section,
        ]

        for builder in builders:
            section = builder()
            if section:
                sections.append(section)

        self._static_cache = "\n\n".join(sections)
        return self._static_cache

    def build_full(self) -> str:
        """Build full prompt = static + DYNAMIC_BOUNDARY + dynamic."""
        return self.build_static() + DYNAMIC_BOUNDARY + self._build_dynamic_section()

    def build_system_reminder(self, task_context: str = "") -> str:
        """Build per-turn reminder for dynamic content injection."""
        return build_system_reminder(task_context=task_context)

    def invalidate_cache(self) -> None:
        """Clear cache to force rebuild on next call."""
        self._static_cache = None

    def _load_section_config(self) -> None:
        """Load section configuration from config.yaml."""
        system_prompt_config = self.config.get("system_prompt", {})
        if not isinstance(system_prompt_config, dict):
            return
        sections = system_prompt_config.get("sections", {})
        if not sections:
            return

        for key in dir(self._section_config):
            if key.startswith("_"):
                continue
            if key == "memory_limit":
                if "memory_limit" in sections:
                    self._section_config.memory_limit = sections["memory_limit"]
                continue
            if key in sections:
                setattr(self._section_config, key, sections[key])
