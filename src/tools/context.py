"""Tool context and gate for execution management."""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ToolGate is now in src.permissions.gate for centralized permission management
# This alias ensures backward compatibility with existing imports
from src.permissions.gate import ToolGate


@dataclass
class ToolContext:
    """Context for tool execution.

    Contains all necessary information for executing a tool,
    including metadata and execution control mechanisms.

    Attributes:
        tool_name: Name of the tool being executed
        args: Arguments passed to the tool
        cwd: Current working directory for execution
        worktree_root: Root directory for worktree isolation (takes precedence over cwd for path resolution)
        tracker: Optional execution tracker for monitoring progress
        gate: Optional gate for controlling mutating operations
        metadata: Additional metadata for the execution context
    """
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    cwd: Path = field(default_factory=Path.cwd)
    worktree_root: Optional[Path] = None
    tracker: Optional[Any] = None
    gate: Optional["ToolGate"] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_gate(self, gate: "ToolGate") -> "ToolContext":
        """Create a new context with the specified gate.

        Args:
            gate: The gate to associate with this context

        Returns:
            A new ToolContext instance with the gate set
        """
        return ToolContext(
            tool_name=self.tool_name,
            args=self.args,
            cwd=self.cwd,
            worktree_root=self.worktree_root,
            tracker=self.tracker,
            gate=gate,
            metadata=self.metadata.copy()
        )
