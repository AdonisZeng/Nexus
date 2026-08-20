"""
Global Hook System for Nexus.

Provides unified hook infrastructure for all components:
- AgentLoop
- ToolOrchestrator
- SubagentRunner

Usage:
    from src.hooks import HookManager, HookRunner, HookEvent

    manager = HookManager()  # Loads global hooks from ~/.nexus/hooks.json
    runner = HookRunner(manager)

    result = await runner.run_pre_tool("bash", {"command": "ls"})
    if result.blocked:
        print("Tool blocked by hook")
"""
from .models import HookEvent, HookDefinition, HookResult
from .manager import HookManager
from .runner import HookRunner
from .config import load_hooks_config, get_hooks_for_event, is_trust_all_enabled

__all__ = [
    "HookEvent",
    "HookDefinition",
    "HookResult",
    "HookManager",
    "HookRunner",
    "load_hooks_config",
    "get_hooks_for_event",
    "is_trust_all_enabled",
]
