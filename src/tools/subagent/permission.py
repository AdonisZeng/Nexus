"""Permission mode enforcement for subagent execution"""
from typing import Optional, TYPE_CHECKING

from src.utils import get_logger

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry

logger = get_logger("subagent.permission")

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

SAFE_TOOLS = {
    "file_read",
    "list_dir",
    "file_search",
    "check_background",
    "load_skill",
}


class PermissionEnforcer:
    """权限模式强制执行器。支持 normal 和 read_only 模式。"""

    def __init__(
        self,
        permission_mode: str = "normal",
        tool_registry: Optional["ToolRegistry"] = None
    ):
        self.permission_mode = permission_mode
        self._tool_registry = tool_registry

    def _is_tool_mutating(self, tool_name: str) -> bool:
        """检查工具是否具有 mutating 属性"""
        if self._tool_registry is not None:
            tool = self._tool_registry.get(tool_name)
            if tool is not None:
                return getattr(tool, "is_mutating", False)
        return False

    def is_tool_allowed(self, tool_name: str) -> tuple[bool, Optional[str]]:
        """检查工具在当前权限模式下是否允许执行。"""
        if self.permission_mode != "read_only":
            return True, None

        if tool_name in SAFE_TOOLS:
            return True, None

        if tool_name in MUTATING_TOOLS:
            return False, f"Tool '{tool_name}' is blocked in read_only mode (mutating tool)"

        # 未知工具 - 在 read_only 模式下保守处理
        if self._is_tool_mutating(tool_name):
            return False, f"Tool '{tool_name}' is blocked in read_only mode (mutating tool)"

        return True, None

    def get_blocked_tools(self) -> set[str]:
        """获取在当前权限模式下会被阻止的工具集合。"""
        if self.permission_mode != "read_only":
            return set()

        blocked = set(MUTATING_TOOLS)
        if self._tool_registry:
            for name, tool in self._tool_registry.tools.items():
                if name not in SAFE_TOOLS and name not in MUTATING_TOOLS:
                    if getattr(tool, "is_mutating", False):
                        blocked.add(name)

        return blocked


__all__ = ["PermissionEnforcer", "MUTATING_TOOLS", "SAFE_TOOLS"]
