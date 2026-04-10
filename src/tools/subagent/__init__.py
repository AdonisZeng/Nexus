"""Subagent module - enables the main agent to invoke subagents"""
from .models import SubagentConfig, SubagentResult, HookDefinition
from .parser import SubagentParser
from .registry import SubagentRegistry
from .runner import SubagentRunner
from .tool import SubagentTool, CheckSubagentTool
from .hooks import HookDefinition, HookResult, HookManager, HookRunner
from .permission import PermissionEnforcer, MUTATING_TOOLS, SAFE_TOOLS
from .parameter_validator import (
    ToolParameterValidator,
    ParameterConstraint,
    MaxLengthConstraint,
    DangerousFlagsConstraint,
    AllowedValuesConstraint,
)
from .background_manager import (
    BackgroundSubagentTask,
    BackgroundSubagentManager,
    get_bg_subagent_manager,
)

__all__ = [
    # Models
    "SubagentConfig",
    "SubagentResult",
    "HookDefinition",
    # Parser
    "SubagentParser",
    # Registry
    "SubagentRegistry",
    # Runner
    "SubagentRunner",
    # Tools
    "SubagentTool",
    "CheckSubagentTool",
    # Hooks
    "HookDefinition",
    "HookResult",
    "HookManager",
    "HookRunner",
    # Permission
    "PermissionEnforcer",
    "MUTATING_TOOLS",
    "SAFE_TOOLS",
    # Parameter validation
    "ToolParameterValidator",
    "ParameterConstraint",
    "MaxLengthConstraint",
    "DangerousFlagsConstraint",
    "AllowedValuesConstraint",
    # Background
    "BackgroundSubagentTask",
    "BackgroundSubagentManager",
    "get_bg_subagent_manager",
]
