"""Tool gate for execution synchronization"""
import asyncio
from typing import Optional


class ToolGate:
    """Gate for controlling access to mutating operations.

    Uses asyncio.Lock to ensure that only one mutating operation
    can execute at a time, preventing race conditions and ensuring
    data consistency.

    Example:
        gate = ToolGate()
        await gate.wait(holder_id=tool.name)
        try:
            # Perform mutating operation
            pass
        finally:
            await gate.release()
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
