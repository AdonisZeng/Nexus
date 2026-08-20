"""Subagent data models"""
from dataclasses import dataclass, field
from typing import Optional, Any
from pathlib import Path


@dataclass
class HookDefinition:
    """Hook definition from frontmatter configuration"""
    command: str
    matcher: Optional[str] = None  # tool name filter, "*" = all


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
    # 新增字段
    cwd: Optional[str] = None  # 隔离工作目录
    env: Optional[dict[str, str]] = None  # 隔离环境变量
    hooks: Optional[dict[str, list[HookDefinition]]] = None  # 生命周期 hooks
    skills: list[str] = field(default_factory=list)  # 可用技能列表
    permission_mode: str = "normal"  # "normal" 或 "read_only"
    tool_parameters: dict[str, dict[str, Any]] = field(default_factory=dict)  # 工具参数限制
    background: bool = False  # 是否后台执行
    # 新增字段
    result_mode: str = "detailed"  # "summary" | "detailed"，控制输出详细程度
    required_tools: list[str] = field(default_factory=list)  # 强制包含的工具
    initial_prompt: str = ""  # 在 system_prompt 之前注入的初始化指令


@dataclass
class SubagentResult:
    """Result returned from a subagent execution"""
    success: bool
    output: str
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    tokens_used: int = 0
    error: Optional[str] = None
