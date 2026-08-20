"""Backward compatibility shim for existing PermissionEnforcer API"""
from typing import TYPE_CHECKING, Optional

from .checker import PermissionChecker
from .modes import PermissionMode
from .registry import MUTATING_TOOLS, SAFE_TOOLS

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


class LegacyPermissionEnforcer:
    """Backward-compatible wrapper for existing PermissionEnforcer API.

    This class preserves the existing `is_tool_allowed(tool_name)` interface
    while delegating to the new PermissionChecker internally.
    """

    def __init__(
        self,
        permission_mode: str = "normal",
        tool_registry: Optional["ToolRegistry"] = None,
    ):
        """Initialize the legacy permission enforcer.

        Args:
            permission_mode: String mode ("normal" or "read_only")
            tool_registry: Optional tool registry for dynamic checks
        """
        try:
            mode = PermissionMode(permission_mode)
        except ValueError:
            mode = PermissionMode.NORMAL
        self._checker = PermissionChecker(mode=mode, tool_registry=tool_registry)
        self._permission_mode = permission_mode

    @property
    def permission_mode(self) -> str:
        """Get the permission mode string."""
        return self._permission_mode

    def is_tool_allowed(self, tool_name: str) -> tuple[bool, Optional[str]]:
        """Check if a tool is allowed (legacy interface).

        Args:
            tool_name: Name of the tool to check

        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
        """
        result = self._checker.check(tool_name)
        return (result.allowed, result.reason)

    def get_blocked_tools(self) -> set[str]:
        """Get the set of tools blocked in the current mode.

        Returns:
            Set of blocked tool names
        """
        return self._checker.get_blocked_tools()


# Re-export constants for backward compatibility
__all__ = [
    "LegacyPermissionEnforcer",
    "PermissionChecker",
    "PermissionMode",
    "MUTATING_TOOLS",
    "SAFE_TOOLS",
]
