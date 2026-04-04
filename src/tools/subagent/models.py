"""Subagent data models"""
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class SubagentConfig:
    """Subagent configuration loaded from .md files"""
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    model: Optional[str] = None
    max_iterations: int = 10
    timeout_seconds: float = 300.0
    file_path: Optional[Path] = None


@dataclass
class SubagentResult:
    """Result returned from a subagent execution"""
    success: bool
    output: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    tokens_used: int = 0
    error: Optional[str] = None
