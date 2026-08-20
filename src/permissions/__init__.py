"""Permission system module.

Unified permission management for tool execution.
The entire implementation lives in core.py.

Public API:
    PermissionChecker - Main permission checking class
    PermissionMode - Enum for permission modes (NORMAL, READ_ONLY, ASK)
    PermissionResult - Result dataclass for permission checks
    PermissionRegistry - Tool classification (mutating vs safe)
    ToolGate - Synchronization primitive for mutating operations
    create_ask_user_callback - Factory for ASK mode user confirmation
"""
from .core import (
    MUTATING_TOOLS,
    SAFE_TOOLS,
    HIGH_RISK_PREFIXES,
    WRITE_PREFIXES,
    READ_PREFIXES,
    AskUserCallback,
    PermissionChecker,
    PermissionMode,
    PermissionRegistry,
    PermissionResult,
    ToolGate,
    add_always_allow_rule,
    clear_always_allow_rules,
    create_ask_user_callback,
    default_ask_user_callback,
    is_always_allowed,
)

__all__ = [
    # Core classes
    "PermissionChecker",
    "PermissionMode",
    "PermissionResult",
    "PermissionRegistry",
    "ToolGate",
    "MUTATING_TOOLS",
    "SAFE_TOOLS",
    "HIGH_RISK_PREFIXES",
    "WRITE_PREFIXES",
    "READ_PREFIXES",
    "AskUserCallback",
    # ASK mode
    "create_ask_user_callback",
    "default_ask_user_callback",
    "add_always_allow_rule",
    "is_always_allowed",
    "clear_always_allow_rules",
]
