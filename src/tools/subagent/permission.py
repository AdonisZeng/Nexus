"""Permission mode enforcement for subagent execution.

This module is maintained for backward compatibility.
New code should use src.permissions instead.
"""
from src.permissions.legacy import LegacyPermissionEnforcer as PermissionEnforcer
from src.permissions import MUTATING_TOOLS, SAFE_TOOLS

__all__ = ["PermissionEnforcer", "MUTATING_TOOLS", "SAFE_TOOLS"]

