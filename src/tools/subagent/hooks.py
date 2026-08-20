"""
Subagent hooks - backward compatibility wrapper.

This module re-exports from src.hooks for backward compatibility
with existing subagent code.
"""
from src.hooks import (
    HookEvent,
    HookDefinition,
    HookResult,
    HookManager,
    HookRunner,
    load_hooks_config,
    get_hooks_for_event,
    is_trust_all_enabled,
)

# Backward compatibility: re-export original names
__all__ = [
    "HookEvent",
    "HookDefinition",
    "HookResult",
    "HookManager",
    "HookRunner",
    "load_hooks_config",
    "get_hooks_for_event",
    "is_trust_all_enabled",
    # Legacy compatibility
    "HOOK_EVENTS",
]

# Legacy constant for backward compatibility
HOOK_EVENTS = (
    "iteration_start",
    "tool_call_start",
    "tool_call_end",
    "terminated",
)
