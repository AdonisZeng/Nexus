"""Task Board - Autonomous task management system for Agent Teams"""
import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum

from src.utils import get_logger

logger = get_logger("team.task_board")


class TaskStatus(str, Enum):
    """Task status enum"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """Task definition for the task board"""
    id: int
    subject: str
    description: str = ""
    status: str = TaskStatus.PENDING.value
    owner: Optional[str] = None
    blocked_by: list = field(default_factory=list)
    spec_file: Optional[str] = None  # Path to shared spec file (e.g., design.md in master root)
    created_at: float = field(default_factory=lambda: __import__('time').time())
    worktree_name: Optional[str] = None
    worktree_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "owner": self.owner,
            "blockedBy": self.blocked_by,
            "specFile": self.spec_file,
            "createdAt": self.created_at,
            "worktreeName": self.worktree_name,
            "worktreePath": self.worktree_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            id=data["id"],
            subject=data["subject"],
            description=data.get("description", ""),
            status=data.get("status", TaskStatus.PENDING.value),
            owner=data.get("owner"),
            blocked_by=data.get("blockedBy", []),
            spec_file=data.get("specFile"),
            created_at=data.get("createdAt", __import__('time').time()),
            worktree_name=data.get("worktreeName"),
            worktree_path=data.get("worktreePath"),
        )


class TaskBoard:
    """Task board manager for autonomous agent teams

    Manages tasks in ~/.nexus/teams/<team_name>/tasks/ directory with JSON files.
    Tasks can be claimed by teammates dynamically.
    """

    def __init__(self, team_name: str, base_dir: Optional[Path] = None):
        """Initialize task board

        Args:
            team_name: Name of the team
            base_dir: Base directory for task storage (defaults to ~/.nexus/teams/<team_name>/tasks/)
        """
        self.team_name = team_name
        if base_dir:
            self.tasks_dir = base_dir / "tasks"
        else:
            self.tasks_dir = Path.home() / ".nexus" / "teams" / team_name / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._next_id = self._load_max_id() + 1

    def _load_max_id(self) -> int:
        """Load the maximum task ID from existing files"""
        max_id = 0
        for f in self.tasks_dir.glob("task_*.json"):
            try:
                task = json.loads(f.read_text())
                max_id = max(max_id, task.get("id", 0))
            except (json.JSONDecodeError, ValueError):
                continue
        return max_id

    def _get_task_path(self, task_id: int) -> Path:
        """Get the file path for a task"""
        return self.tasks_dir / f"task_{task_id}.json"

    def add_task(
        self,
        subject: str,
        description: str = "",
        blocked_by: Optional[list] = None,
        spec_file: Optional[str] = None
    ) -> Task:
        """Add a new task to the board

        Args:
            subject: Task subject/title
            description: Detailed description
            blocked_by: List of task IDs this task is blocked by
            spec_file: Path to shared spec file (e.g., design.md in master root)

        Returns:
            Created Task object
        """
        with self._lock:
            task = Task(
                id=self._next_id,
                subject=subject,
                description=description,
                blocked_by=blocked_by or [],
                spec_file=spec_file,
            )
            self._next_id += 1

            path = self._get_task_path(task.id)
            path.write_text(json.dumps(task.to_dict(), indent=2))
            blocked_info = f" (blocked_by: #{', '.join(map(str, task.blocked_by))})" if task.blocked_by else ""
            logger.info(f"[TaskBoard] Task #{task.id}: {task.subject}{blocked_info} added to team '{self.team_name}'")
            return task

    def get_task(self, task_id: int) -> Optional[Task]:
        """Get a task by ID

        Args:
            task_id: Task ID

        Returns:
            Task object or None if not found
        """
        path = self._get_task_path(task_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return Task.from_dict(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Error loading task #{task_id}: {e}")
            return None

    def get_all_tasks(self) -> list[Task]:
        """Get all tasks

        Returns:
            List of all Task objects
        """
        tasks = []
        for f in sorted(self.tasks_dir.glob("task_*.json")):
            try:
                task = Task.from_dict(json.loads(f.read_text()))
                tasks.append(task)
            except (json.JSONDecodeError, ValueError):
                continue
        return sorted(tasks, key=lambda x: x.id)

    def scan_unclaimed(self) -> list[Task]:
        """Scan for unclaimed tasks (pending and not owned)

        Returns:
            List of unclaimed Task objects
        """
        unclaimed = []
        for f in sorted(self.tasks_dir.glob("task_*.json")):
            try:
                task = Task.from_dict(json.loads(f.read_text()))
                if task.status == TaskStatus.PENDING.value and not task.owner:
                    if not self._has_blockers(task):
                        unclaimed.append(task)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Error loading task from {f}: {e}")
                continue
        return unclaimed

    def _has_blockers(self, task: Task) -> bool:
        """Check if a task has uncompleted blocking tasks"""
        for blocker_id in task.blocked_by:
            blocker = self.get_task(blocker_id)
            if blocker and blocker.status != TaskStatus.COMPLETED.value:
                logger.debug(f"[TaskBoard] Task #{task.id} is blocked by incomplete task #{blocker_id}")
                return True
        return False

    def get_blocker_status(self, task_id: int) -> dict:
        """Get the status of all blockers for a task

        Returns:
            dict with keys:
            - 'can_proceed': bool, True if all blockers are completed
            - 'blockers': list of incomplete blocker info, each with id, subject, status
        """
        task = self.get_task(task_id)
        if not task:
            return {'can_proceed': False, 'blockers': []}

        incomplete = []
        for blocker_id in task.blocked_by:
            blocker = self.get_task(blocker_id)
            if blocker and blocker.status != TaskStatus.COMPLETED.value:
                incomplete.append({
                    'id': blocker.id,
                    'subject': blocker.subject,
                    'status': blocker.status
                })

        return {
            'can_proceed': len(incomplete) == 0,
            'blockers': incomplete
        }

    def get_member_current_task(self, member_name: str) -> Optional[Task]:
        """Get the current in_progress task for a member

        Args:
            member_name: Name of the teammate

        Returns:
            The Task that is currently in_progress and owned by this member, or None
        """
        for f in self.tasks_dir.glob("task_*.json"):
            try:
                task = Task.from_dict(json.loads(f.read_text()))
                if task.owner == member_name and task.status == TaskStatus.IN_PROGRESS.value:
                    return task
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def claim(self, task_id: int, owner: str) -> bool:
        """Claim a task for a teammate

        Args:
            task_id: Task ID to claim
            owner: Name of the teammate claiming the task

        Returns:
            True if successfully claimed, False otherwise
        """
        with self._lock:
            task = self.get_task(task_id)
            if not task:
                logger.warning(f"Cannot claim: Task #{task_id} not found")
                return False

            if task.status != TaskStatus.PENDING.value:
                logger.warning(f"Cannot claim: Task #{task_id} is {task.status}")
                return False

            if task.owner:
                logger.warning(f"Cannot claim: Task #{task_id} already owned by {task.owner}")
                return False

            if self._has_blockers(task):
                logger.warning(f"Cannot claim: Task #{task_id} has uncompleted blockers")
                return False

            task.owner = owner
            task.status = TaskStatus.IN_PROGRESS.value

            path = self._get_task_path(task_id)
            path.write_text(json.dumps(task.to_dict(), indent=2))
            logger.info(f"[TaskBoard] Task #{task_id}: {task.subject} claimed by {owner}")
            return True

    def scan_and_claim(self, owner: str) -> Optional[Task]:
        """Atomically scan for and claim the first available task.

        This prevents race conditions when multiple teammates try to claim
        the same task simultaneously.

        Args:
            owner: Name of the teammate claiming the task

        Returns:
            The claimed Task object, or None if no task is available
        """
        with self._lock:
            for f in sorted(self.tasks_dir.glob("task_*.json")):
                try:
                    task = Task.from_dict(json.loads(f.read_text()))
                    if (task.status == TaskStatus.PENDING.value
                        and not task.owner
                        and not self._has_blockers(task)):
                        task.owner = owner
                        task.status = TaskStatus.IN_PROGRESS.value
                        f.write_text(json.dumps(task.to_dict(), indent=2))
                        logger.info(f"[TaskBoard] Task #{task.id}: {task.subject} auto-claimed by {owner}")
                        return task
                except (json.JSONDecodeError, ValueError):
                    continue
            return None

    def complete(self, task_id: int) -> bool:
        """Mark a task as completed

        Args:
            task_id: Task ID to complete

        Returns:
            True if successfully completed, False otherwise
        """
        with self._lock:
            task = self.get_task(task_id)
            if not task:
                logger.warning(f"Cannot complete: Task #{task_id} not found")
                return False

            task.status = TaskStatus.COMPLETED.value

            path = self._get_task_path(task_id)
            path.write_text(json.dumps(task.to_dict(), indent=2))
            logger.info(f"[TaskBoard] Task #{task_id}: {task.subject} marked as completed")
            return True

    def release(self, task_id: int) -> bool:
        """Release a claimed task back to pending

        Args:
            task_id: Task ID to release

        Returns:
            True if successfully released, False otherwise
        """
        with self._lock:
            task = self.get_task(task_id)
            if not task:
                return False

            if task.status != TaskStatus.IN_PROGRESS.value:
                return False

            task.status = TaskStatus.PENDING.value
            task.owner = None

            path = self._get_task_path(task_id)
            path.write_text(json.dumps(task.to_dict(), indent=2))
            logger.info(f"[TaskBoard] Task #{task_id}: {task.subject} released back to pending")
            return True

    def bind_worktree(self, task_id: int, worktree_name: str, worktree_path: str) -> bool:
        """Bind a worktree to a task

        Args:
            task_id: Task ID to bind worktree to
            worktree_name: Name of the worktree
            worktree_path: Path to the worktree

        Returns:
            True if successfully bound, False otherwise
        """
        with self._lock:
            task = self.get_task(task_id)
            if not task:
                logger.warning(f"Cannot bind worktree: Task #{task_id} not found")
                return False

            task.worktree_name = worktree_name
            task.worktree_path = worktree_path

            path = self._get_task_path(task_id)
            path.write_text(json.dumps(task.to_dict(), indent=2))
            logger.info(f"Task #{task_id} bound to worktree '{worktree_name}' at {worktree_path}")
            return True

    def get_status(self) -> dict:
        """Get task board status

        Returns:
            Dictionary with status information
        """
        tasks = []
        for f in sorted(self.tasks_dir.glob("task_*.json")):
            try:
                task = Task.from_dict(json.loads(f.read_text()))
                tasks.append(task)
            except (json.JSONDecodeError, ValueError):
                continue

        pending = sum(1 for t in tasks if t.status == TaskStatus.PENDING.value)
        in_progress = sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS.value)
        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED.value)

        return {
            "team": self.team_name,
            "total": len(tasks),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "tasks": [t.to_dict() for t in sorted(tasks, key=lambda x: x.id)],
        }

    def format_status(self) -> str:
        """Format task board status as a readable string

        Returns:
            Formatted status string
        """
        status = self.get_status()

        lines = [
            f"Task Board: {status['team']}",
            f"Total: {status['total']} | Pending: {status['pending']} | In Progress: {status['in_progress']} | Completed: {status['completed']}",
            ""
        ]

        marker_map = {
            TaskStatus.PENDING.value: "[ ]",
            TaskStatus.IN_PROGRESS.value: "[>]",
            TaskStatus.COMPLETED.value: "[x]",
        }

        for task in status["tasks"]:
            marker = marker_map.get(task["status"], "[?]")
            owner = f" @{task['owner']}" if task.get("owner") else ""
            blocked = " (blocked)" if task.get("blockedBy") else ""
            worktree = f" [worktree: {task['worktreeName']}]" if task.get("worktreeName") else ""
            lines.append(f"  {marker} #{task['id']}: {task['subject']}{owner}{blocked}{worktree}")

        return "\n".join(lines)

    @staticmethod
    def scan_all_unclaimed(task_boards: dict[str, "TaskBoard"]) -> tuple[Optional["TaskBoard"], Optional[Task]]:
        """Scan all task boards for any unclaimed task

        Args:
            task_boards: Dict of team_name -> TaskBoard

        Returns:
            Tuple of (TaskBoard, Task) or (None, None) if no unclaimed tasks
        """
        for team_name, board in task_boards.items():
            unclaimed = board.scan_unclaimed()
            if unclaimed:
                return board, unclaimed[0]
        return None, None
