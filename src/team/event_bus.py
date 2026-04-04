"""Event bus module - Event logging and querying for team activities"""
import json
from datetime import datetime
from typing import Optional

from .database import Database


class EventBus:
    """Event bus for logging and querying team events"""

    def __init__(self, team_name: str, db_path: Optional[str] = None):
        """
        @brief Initialize EventBus with database connection

        @param team_name Name of the team
        @param db_path Optional custom database path
        """
        self._db = Database(team_name, db_path)

    def emit(
        self,
        event_type: str,
        task_id: Optional[int] = None,
        worktree_name: Optional[str] = None,
        **kwargs
    ) -> int:
        """
        @brief Record an event

        @param event_type Type of the event
        @param task_id Associated task ID
        @param worktree_name Associated worktree name
        @param kwargs Additional metadata
        @return ID of the inserted event
        """
        metadata = kwargs if kwargs else None
        return self._db.insert_event(
            event_type=event_type,
            task_id=task_id,
            worktree_name=worktree_name,
            metadata=metadata,
        )

    def list_recent(
        self,
        limit: int = 20,
        event_type: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> list[dict]:
        """
        @brief Query recent events

        @param limit Maximum number of events to return (default 20)
        @param event_type Filter by event type
        @param task_id Filter by task ID
        @return List of event records
        """
        return self._db.get_events(
            event_type=event_type,
            task_id=task_id,
            limit=limit,
        )

    def format_events(self, events: list[dict]) -> str:
        """
        @brief Format events for readable output

        @param events List of event records
        @return Formatted string representation
        """
        if not events:
            return "No events found."

        lines = []
        for event in events:
            timestamp = datetime.fromtimestamp(event["created_at"])
            time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

            parts = [f"[{time_str}] {event['event_type']}"]

            if event.get("task_id"):
                parts.append(f"task={event["task_id"]}")
            if event.get("worktree_name"):
                parts.append(f"worktree={event["worktree_name"]}")
            if event.get("metadata"):
                try:
                    metadata = json.loads(event["metadata"])
                    if metadata:
                        parts.append(f"data={metadata}")
                except (json.JSONDecodeError, TypeError):
                    pass

            lines.append(" | ".join(parts))

        return "\n".join(lines)

    def close(self) -> None:
        """@brief Close the database connection"""
        self._db.close()

    def __enter__(self) -> "EventBus":
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit"""
        self._db.close()
