"""
Work Item Source - Abstract interface for mode-specific work item acquisition.

This module provides the WorkItem dataclass and WorkItemSource abstract class
for unifying how different modes get their next unit of work.
"""

from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import Optional, Any


@dataclass
class WorkItem:
    """Represents a unit of work for the agent to process."""

    id: str
    """Unique identifier for this work item."""

    description: str
    """Human-readable description of the work item."""

    context: Any = None
    """Mode-specific context data (e.g., task object, message, etc.)."""

    metadata: dict = field(default_factory=dict)
    """Additional metadata for the work item."""


class WorkItemSource(ABC):
    """Abstract interface for getting work items in different modes.

    Each mode (Plan, Tasks, Teammate, Subagent) implements this interface
    to provide mode-specific work item acquisition logic.
    """

    @abstractmethod
    async def get_next_work_item(self) -> Optional[WorkItem]:
        """Get the next work item, or None if no more work.

        Returns:
            WorkItem if there's more work to process, None if done.
        """
        pass

    @abstractmethod
    async def on_work_item_completed(self, item: WorkItem, result: str) -> None:
        """Called when a work item has been completed.

        Args:
            item: The work item that was completed.
            result: The result/output of processing this item.
        """
        pass

    @abstractmethod
    def has_more_work(self) -> bool:
        """Check if there is more work to process.

        Returns:
            True if there are more work items, False otherwise.
        """
        pass


__all__ = ["WorkItem", "WorkItemSource"]
