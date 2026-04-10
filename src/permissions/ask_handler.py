"""ASK mode handler - async user confirmation for permission system"""
import asyncio
import json
from typing import Callable, Optional


# Consecutive denial tracking for circuit breaker
_consecutive_denials = 0
_max_consecutive_denials = 3


def _reset_consecutive_denials():
    """Reset consecutive denials counter."""
    global _consecutive_denials
    _consecutive_denials = 0


def _increment_denials():
    """Increment and return consecutive denials count."""
    global _consecutive_denials
    _consecutive_denials += 1
    return _consecutive_denials


# Permanent allow rules storage
_always_allow_rules: list[dict] = []


def add_always_allow_rule(tool_name: str, path: str = "*") -> None:
    """Add a permanent allow rule for a tool.

    Args:
        tool_name: Name of the tool to always allow
        path: Optional path pattern to allow
    """
    _always_allow_rules.append({"tool": tool_name, "path": path, "behavior": "allow"})


def is_always_allowed(tool_name: str) -> bool:
    """Check if a tool has an always-allow rule.

    Args:
        tool_name: Name of the tool to check

    Returns:
        True if tool has an always-allow rule
    """
    return any(rule.get("tool") == tool_name and rule.get("behavior") == "allow"
               for rule in _always_allow_rules)


def clear_always_allow_rules() -> None:
    """Clear all always-allow rules."""
    global _always_allow_rules
    _always_allow_rules = []


async def default_ask_user_callback(tool_name: str, tool_input: dict) -> bool:
    """Default ASK callback using run_in_executor for non-blocking input.

    This is the default implementation for permission confirmation in ASK mode.
    It runs the synchronous input() in a thread pool to avoid blocking the event loop.

    Args:
        tool_name: Name of the tool requesting permission
        tool_input: Arguments passed to the tool

    Returns:
        True if user approved, False otherwise
    """
    # Check always-allow rules first
    if is_always_allowed(tool_name):
        _reset_consecutive_denials()
        return True

    loop = asyncio.get_event_loop()

    def sync_input():
        """Synchronous input prompt - runs in executor thread."""
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

    # Denied
    denials = _increment_denials()
    if denials >= _max_consecutive_denials:
        print(f"  [{denials} consecutive denials -- consider switching to plan mode]")
    return False


def create_ask_user_callback(
    max_denials: int = 3,
    rules: Optional[list[dict]] = None
) -> Callable[[str, dict], bool]:
    """Factory to create a configured ask user callback.

    Args:
        max_denials: Maximum consecutive denials before warning
        rules: Initial always-allow rules

    Returns:
        Coroutine function for user confirmation
    """
    global _max_consecutive_denials, _always_allow_rules
    _max_consecutive_denials = max_denials
    if rules:
        _always_allow_rules = rules.copy()

    return default_ask_user_callback
