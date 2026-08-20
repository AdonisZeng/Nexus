"""Permission system module.

This module provides unified permission management for tool execution,
replacing scattered permission logic across SubagentRunner and other components.

Public API:
    PermissionChecker - Main permission checking class
    PermissionMode - Enum for permission modes (NORMAL, READ_ONLY, ASK)
    PermissionResult - Result dataclass for permission checks
    PermissionRegistry - Tool classification (mutating vs safe)
    ToolGate - Synchronization primitive for mutating operations
    LegacyPermissionEnforcer - Backward-compatible wrapper
    create_ask_user_callback - Factory for ASK mode user confirmation
"""
from .ask_handler import (
    create_ask_user_callback,
    default_ask_user_callback,
    add_always_allow_rule,
    clear_always_allow_rules,
)
from .checker import PermissionChecker
from .gate import ToolGate
from .legacy import LegacyPermissionEnforcer
from .modes import PermissionMode
from .registry import MUTATING_TOOLS, SAFE_TOOLS, PermissionRegistry
from .result import PermissionResult

__all__ = [
    # Core classes
    "PermissionChecker",
    "PermissionMode",
    "PermissionResult",
    "PermissionRegistry",
    "ToolGate",
    # Backward compatibility
    "LegacyPermissionEnforcer",
    "MUTATING_TOOLS",
    "SAFE_TOOLS",
    # ASK mode
    "create_ask_user_callback",
    "default_ask_user_callback",
    "add_always_allow_rule",
    "clear_always_allow_rules",
]
