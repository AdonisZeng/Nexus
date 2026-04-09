"""Tools module"""
from .registry import ModelProviderMixin, Tool, ToolRegistry
from .file import FileReadTool, FileWriteTool, FileSearchTool
from .shell import ShellTool
from .code import CodeExecTool
from .subagent import SubagentTool, SubagentRegistry
from src.team.tools import TeamTool

__all__ = [
    "ModelProviderMixin",
    "Tool",
    "ToolRegistry",
    "global_registry",
    "FileReadTool",
    "FileWriteTool",
    "FileSearchTool",
    "ShellTool",
    "CodeExecTool",
    "SubagentTool",
    "SubagentRegistry",
    "TeamTool",
]

# Re-export the single authoritative instance from registry (avoid double-instantiation)
from .registry import global_registry