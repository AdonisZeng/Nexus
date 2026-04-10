"""Permission checker - unified permission interface"""
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from .modes import PermissionMode
from .registry import PermissionRegistry
from .result import PermissionResult

if TYPE_CHECKING:
    from src.tools.registry import Tool, ToolRegistry

logger = logging.getLogger("permissions.checker")

# Type alias for ask user callback
AskUserCallback = Callable[[str, dict], Awaitable[bool]]


class PermissionChecker:
    """Unified permission interface for all permission modes.

    This class centralizes permission checks for tool execution,
    replacing scattered permission logic across SubagentRunner and other components.
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.NORMAL,
        tool_registry: Optional["ToolRegistry"] = None,
        ask_user_callback: Optional["AskUserCallback"] = None,
    ):
        """Initialize the permission checker.

        Args:
            mode: Permission mode (NORMAL, READ_ONLY, or ASK)
            tool_registry: Optional tool registry for dynamic is_mutating checks
            ask_user_callback: Optional async callback for ASK mode user confirmation
        """
        self._mode = mode
        self._registry = tool_registry
        self._ask_user_callback = ask_user_callback
        self._cache: dict[str, bool] = {}

    @property
    def mode(self) -> PermissionMode:
        """Get current permission mode."""
        return self._mode

    @mode.setter
    def mode(self, value: PermissionMode) -> None:
        """Set permission mode."""
        self._mode = value
        self._cache.clear()

    @property
    def ask_user_callback(self) -> Optional["AskUserCallback"]:
        """Get the ask user callback for ASK mode."""
        return self._ask_user_callback

    def check(self, tool_name: str) -> PermissionResult:
        """Check if a tool is allowed in the current permission mode.

        This method uses the MUTATING_TOOLS/SAFE_TOOLS sets for lookup.
        For tool-based checking with is_mutating property, use check_with_registry().

        Args:
            tool_name: Name of the tool to check

        Returns:
            PermissionResult indicating if the tool is allowed
        """
        # NORMAL mode: allow everything
        if self._mode == PermissionMode.NORMAL:
            return PermissionResult(
                allowed=True,
                reason="Normal mode: all tools allowed",
                mode_applied=self._mode.value,
            )

        # READ_ONLY mode: check if mutating
        if self._mode == PermissionMode.READ_ONLY:
            # Check cache first
            if tool_name in self._cache:
                allowed = self._cache[tool_name]
                return PermissionResult(
                    allowed=allowed,
                    reason="Cached result" if allowed else f"Tool '{tool_name}' is blocked in read_only mode",
                    mode_applied=self._mode.value,
                )

            is_mutating = PermissionRegistry.is_mutating(tool_name)
            self._cache[tool_name] = not is_mutating

            if is_mutating:
                return PermissionResult(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is blocked in read_only mode (mutating tool)",
                    mode_applied=self._mode.value,
                )
            else:
                return PermissionResult(
                    allowed=True,
                    reason=f"Tool '{tool_name}' is safe in read_only mode",
                    mode_applied=self._mode.value,
                )

        # ASK mode: requires user confirmation via callback
        if self._mode == PermissionMode.ASK:
            if self._ask_user_callback:
                return PermissionResult(
                    allowed=False,
                    reason="Awaiting user confirmation",
                    mode_applied=self._mode.value,
                    needs_confirmation=True,
                )
            else:
                return PermissionResult(
                    allowed=False,
                    reason="ASK mode requires user callback",
                    mode_applied=self._mode.value,
                )

        # Fallback
        return PermissionResult(
            allowed=True,
            reason="Unknown mode, allowing by default",
            mode_applied="unknown",
        )

    def check_with_tool(self, tool: "Tool") -> PermissionResult:
        """Check permission using a Tool instance directly.

        This method uses the tool's is_mutating property for accurate checking.

        Args:
            tool: Tool instance to check

        Returns:
            PermissionResult indicating if the tool is allowed
        """
        tool_name = tool.name

        # NORMAL mode: allow everything
        if self._mode == PermissionMode.NORMAL:
            return PermissionResult(
                allowed=True,
                reason="Normal mode: all tools allowed",
                mode_applied=self._mode.value,
            )

        # READ_ONLY mode: check is_mutating property
        if self._mode == PermissionMode.READ_ONLY:
            is_mutating = getattr(tool, "is_mutating", False)

            if is_mutating:
                return PermissionResult(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is blocked in read_only mode (mutating tool)",
                    mode_applied=self._mode.value,
                )
            else:
                return PermissionResult(
                    allowed=True,
                    reason=f"Tool '{tool_name}' is allowed in read_only mode",
                    mode_applied=self._mode.value,
                )

        # ASK mode: requires user confirmation via callback
        if self._mode == PermissionMode.ASK:
            if self._ask_user_callback:
                return PermissionResult(
                    allowed=False,
                    reason="Awaiting user confirmation",
                    mode_applied=self._mode.value,
                    needs_confirmation=True,
                )
            else:
                return PermissionResult(
                    allowed=False,
                    reason="ASK mode requires user callback",
                    mode_applied=self._mode.value,
                )

        return PermissionResult(
            allowed=True,
            reason="Unknown mode, allowing by default",
            mode_applied="unknown",
        )

    def get_blocked_tools(self) -> set[str]:
        """Get the set of tools blocked in the current mode.

        Returns:
            Set of blocked tool names (empty in NORMAL mode)
        """
        if self._mode == PermissionMode.READ_ONLY:
            return PermissionRegistry.get_blocked_tools()
        return set()
