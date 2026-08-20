"""Permission system core: modes, results, tool classification, checker, ask handler, gate."""
import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from src.tools.registry import Tool, ToolRegistry

logger = logging.getLogger("permissions.core")


# ---------------------------------------------------------------------------
# Modes & results
# ---------------------------------------------------------------------------

class PermissionMode(Enum):
    """Permission modes for controlling tool execution."""

    NORMAL = "normal"
    READ_ONLY = "read_only"
    ASK = "ask"

    @classmethod
    def from_string(cls, value: str) -> "PermissionMode":
        """Parse a string into a PermissionMode."""
        try:
            return cls(value)
        except ValueError:
            valid = [m.value for m in cls]
            raise ValueError(f"Unknown mode: {value}. Choose from {valid}")


@dataclass
class PermissionResult:
    """Result of a permission check."""

    allowed: bool
    reason: Optional[str] = None
    mode_applied: str = "normal"
    needs_confirmation: bool = False  # ASK mode requires user interaction

    def __bool__(self) -> bool:
        return self.allowed


# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------

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

# Tool name prefixes for risk classification (shared with MCP adapter)
HIGH_RISK_PREFIXES = ("delete", "remove", "drop", "shutdown", "destroy")
WRITE_PREFIXES = ("create", "write", "update", "edit", "modify", "add")
READ_PREFIXES = ("read", "list", "get", "show", "search", "query", "inspect")


class PermissionRegistry:
    """Registry mapping tools to permission categories."""

    @classmethod
    def is_mutating(cls, tool_name: str, tool: Optional["Tool"] = None) -> bool:
        """Determine if a tool is mutating.

        Priority: tool.is_mutating property > MUTATING_TOOLS set > False.
        """
        if tool is not None:
            return getattr(tool, "is_mutating", False)
        if tool_name in MUTATING_TOOLS:
            return True
        if tool_name in SAFE_TOOLS:
            return False
        # Default: assume safe (conservative for unknown tools)
        return False

    @classmethod
    def is_safe(cls, tool_name: str) -> bool:
        """Check if a tool is explicitly marked as safe."""
        return tool_name in SAFE_TOOLS

    @classmethod
    def get_blocked_tools(cls) -> set[str]:
        """Get the set of tools blocked in read_only mode."""
        return set(MUTATING_TOOLS)


# ---------------------------------------------------------------------------
# Permission checker
# ---------------------------------------------------------------------------

# Type alias for ask user callback
AskUserCallback = Callable[[str, dict], Awaitable[bool]]


class PermissionChecker:
    """Unified permission interface for all permission modes."""

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.NORMAL,
        tool_registry: Optional["ToolRegistry"] = None,
        ask_user_callback: Optional[Callable] = None,
    ):
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
    def ask_user_callback(self) -> Optional[Callable]:
        """Get the ask user callback for ASK mode."""
        return self._ask_user_callback

    def check(self, tool_name: str) -> PermissionResult:
        """Check if a tool is allowed in the current permission mode."""
        # NORMAL mode: allow everything
        if self._mode == PermissionMode.NORMAL:
            return PermissionResult(
                allowed=True,
                reason="Normal mode: all tools allowed",
                mode_applied=self._mode.value,
            )

        # READ_ONLY mode: check if mutating
        if self._mode == PermissionMode.READ_ONLY:
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

    def check_with_tool(self, tool: "Tool") -> PermissionResult:
        """Check permission using a Tool instance's is_mutating property."""
        tool_name = tool.name

        if self._mode == PermissionMode.NORMAL:
            return PermissionResult(
                allowed=True,
                reason="Normal mode: all tools allowed",
                mode_applied=self._mode.value,
            )

        if self._mode == PermissionMode.READ_ONLY:
            if getattr(tool, "is_mutating", False):
                return PermissionResult(
                    allowed=False,
                    reason=f"Tool '{tool_name}' is blocked in read_only mode (mutating tool)",
                    mode_applied=self._mode.value,
                )
            return PermissionResult(
                allowed=True,
                reason=f"Tool '{tool_name}' is allowed in read_only mode",
                mode_applied=self._mode.value,
            )

        if self._mode == PermissionMode.ASK:
            if self._ask_user_callback:
                return PermissionResult(
                    allowed=False,
                    reason="Awaiting user confirmation",
                    mode_applied=self._mode.value,
                    needs_confirmation=True,
                )
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
        """Get the set of tools blocked in the current mode."""
        if self._mode == PermissionMode.READ_ONLY:
            return PermissionRegistry.get_blocked_tools()
        return set()


# ---------------------------------------------------------------------------
# ASK mode handler
# ---------------------------------------------------------------------------

# Consecutive denial tracking for circuit breaker
_consecutive_denials = 0
_max_consecutive_denials = 3

# Permanent allow rules storage
_always_allow_rules: list[dict] = []


def _reset_consecutive_denials():
    """Reset consecutive denials counter."""
    global _consecutive_denials
    _consecutive_denials = 0


def _increment_denials():
    """Increment and return consecutive denials count."""
    global _consecutive_denials
    _consecutive_denials += 1
    return _consecutive_denials


def add_always_allow_rule(tool_name: str, path: str = "*") -> None:
    """Add a permanent allow rule for a tool."""
    _always_allow_rules.append({"tool": tool_name, "path": path, "behavior": "allow"})


def is_always_allowed(tool_name: str) -> bool:
    """Check if a tool has an always-allow rule."""
    return any(rule.get("tool") == tool_name and rule.get("behavior") == "allow"
               for rule in _always_allow_rules)


def clear_always_allow_rules() -> None:
    """Clear all always-allow rules."""
    global _always_allow_rules
    _always_allow_rules = []


async def default_ask_user_callback(tool_name: str, tool_input: dict) -> bool:
    """Default ASK callback; runs input() in an executor to avoid blocking."""
    if is_always_allowed(tool_name):
        _reset_consecutive_denials()
        return True

    loop = asyncio.get_event_loop()

    def sync_input():
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n  [Permission] {tool_name}: {preview}")
        try:
            answer = input("  Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "n"
        return answer

    try:
        answer = await loop.run_in_executor(None, sync_input)
    except Exception:
        return False

    if answer in ("y", "yes"):
        _reset_consecutive_denials()
        return True

    if answer == "always":
        add_always_allow_rule(tool_name)
        _reset_consecutive_denials()
        print(f"  [Permission] {tool_name} always allowed")
        return True

    denials = _increment_denials()
    if denials >= _max_consecutive_denials:
        print(f"  [{denials} consecutive denials -- consider switching to plan mode]")
    return False


def create_ask_user_callback(
    max_denials: int = 3,
    rules: Optional[list[dict]] = None
) -> Callable[[str, dict], bool]:
    """Factory to create a configured ask user callback."""
    global _max_consecutive_denials, _always_allow_rules
    _max_consecutive_denials = max_denials
    if rules:
        _always_allow_rules = rules.copy()

    return default_ask_user_callback


# ---------------------------------------------------------------------------
# Tool gate
# ---------------------------------------------------------------------------

class ToolGate:
    """Gate controlling access to mutating operations via asyncio.Lock."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._holder: Optional[str] = None

    async def wait(self, holder_id: Optional[str] = None) -> None:
        """Wait to acquire the execution lock."""
        await self._lock.acquire()
        self._holder = holder_id

    async def release(self) -> None:
        """Release the execution lock.

        Raises:
            RuntimeError: If the lock is not held when release is called
        """
        if not self._lock.locked():
            raise RuntimeError("Cannot release an unheld lock")
        self._holder = None
        self._lock.release()

    @property
    def is_locked(self) -> bool:
        """Check if the gate is currently locked."""
        return self._lock.locked()

    @property
    def holder(self) -> Optional[str]:
        """Get the current holder of the lock."""
        return self._holder


__all__ = [
    "PermissionMode",
    "PermissionResult",
    "MUTATING_TOOLS",
    "SAFE_TOOLS",
    "HIGH_RISK_PREFIXES",
    "WRITE_PREFIXES",
    "READ_PREFIXES",
    "PermissionRegistry",
    "AskUserCallback",
    "PermissionChecker",
    "add_always_allow_rule",
    "is_always_allowed",
    "clear_always_allow_rules",
    "default_ask_user_callback",
    "create_ask_user_callback",
    "ToolGate",
]
