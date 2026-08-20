"""Permission registry for tool classification"""
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.tools.registry import Tool


# Tools that modify state (files, data, etc.)
MUTATING_TOOLS = {
    "file_write",
    "file_patch",
    "shell_run",
    "code_exec",
    "todo_add",
    "todo_update",
    "todo_delete",
    "background_run",
    "subagent",
    "team",
    "task",
}

# Tools that only read information
SAFE_TOOLS = {
    "file_read",
    "list_dir",
    "file_search",
    "check_background",
    "load_skill",
}

# Tool name prefixes for risk classification (shared across MCP adapter and capability gate)
HIGH_RISK_PREFIXES = ("delete", "remove", "drop", "shutdown", "destroy")
WRITE_PREFIXES = ("create", "write", "update", "edit", "modify", "add")
READ_PREFIXES = ("read", "list", "get", "show", "search", "query", "inspect")


class PermissionRegistry:
    """Registry mapping tools to permission categories.

    Provides definitive source of truth for which tools are mutating,
    replacing scattered MUTATING_TOOLS/SAFE_TOOLS constants.
    """

    @classmethod
    def is_mutating(cls, tool_name: str, tool: Optional["Tool"] = None) -> bool:
        """Determine if a tool is mutating.

        Priority:
        1. tool.is_mutating property (if tool provided)
        2. Check MUTATING_TOOLS set
        3. Fall back to False (optimistically safe)

        Args:
            tool_name: Name of the tool
            tool: Tool instance (optional)

        Returns:
            True if the tool is mutating
        """
        # Priority 1: Check tool's own is_mutating property
        if tool is not None:
            return getattr(tool, "is_mutating", False)

        # Priority 2: Check known mutating tools set
        if tool_name in MUTATING_TOOLS:
            return True

        # Priority 3: Check known safe tools set (explicit safety)
        if tool_name in SAFE_TOOLS:
            return False

        # Default: assume safe (conservative for unknown tools)
        return False

    @classmethod
    def is_safe(cls, tool_name: str) -> bool:
        """Check if a tool is explicitly marked as safe.

        Args:
            tool_name: Name of the tool

        Returns:
            True if the tool is in the SAFE_TOOLS set
        """
        return tool_name in SAFE_TOOLS

    @classmethod
    def get_blocked_tools(cls) -> set[str]:
        """Get the set of tools blocked in read_only mode.

        Returns:
            Set of mutating tool names
        """
        return set(MUTATING_TOOLS)
