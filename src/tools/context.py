"""Tool context and gate for execution management."""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ToolContext:
    """Context for tool execution.

    Contains all necessary information for executing a tool,
    including metadata and execution control mechanisms.

    Attributes:
        tool_name: Name of the tool being executed
        args: Arguments passed to the tool
        cwd: Current working directory for execution
        tracker: Optional execution tracker for monitoring progress
        gate: Optional gate for controlling mutating operations
        metadata: Additional metadata for the execution context
    """
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    cwd: Path = field(default_factory=Path.cwd)
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
            tracker=self.tracker,
            gate=gate,
            metadata=self.metadata.copy()
        )


class ToolGate:
    """Gate for controlling access to mutating operations.

    Uses asyncio.Lock to ensure that only one mutating operation
    can execute at a time, preventing race conditions and ensuring
    data consistency.

    Example:
        gate = ToolGate()
        await gate.wait()  # Acquire the lock
        try:
            # Perform mutating operation
            pass
        finally:
            await gate.release()  # Release the lock
    """

    def __init__(self):
        """Initialize the gate with an asyncio Lock."""
        self._lock = asyncio.Lock()
        self._holder: Optional[str] = None

    async def wait(self, holder_id: Optional[str] = None) -> None:
        """Wait to acquire the execution lock.

        This method blocks until the lock is available.
        Use this before performing mutating operations.

        Args:
            holder_id: Optional identifier for the lock holder
        """
        await self._lock.acquire()
        self._holder = holder_id

    async def release(self) -> None:
        """Release the execution lock.

        This method releases the lock, allowing other waiting
        operations to proceed. Should be called after wait()
        when the mutating operation is complete.

        Raises:
            RuntimeError: If the lock is not held when release is called
        """
        if not self._lock.locked():
            raise RuntimeError("Cannot release an unheld lock")
        self._holder = None
        self._lock.release()

    @property
    def is_locked(self) -> bool:
        """Check if the gate is currently locked.

        Returns:
            True if the gate is locked, False otherwise
        """
        return self._lock.locked()

    @property
    def holder(self) -> Optional[str]:
        """Get the current holder of the lock.

        Returns:
            The holder identifier if set, None otherwise
        """
        return self._holder
