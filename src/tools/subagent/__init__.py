"""Subagent module - enables the main agent to invoke subagents"""
from .models import SubagentConfig, SubagentResult
from .parser import SubagentParser
from .registry import SubagentRegistry
from .runner import SubagentRunner
from .tool import SubagentTool

__all__ = [
    "SubagentConfig",
    "SubagentResult",
    "SubagentParser",
    "SubagentRegistry",
    "SubagentRunner",
    "SubagentTool",
]
