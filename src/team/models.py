"""Team data models"""
from dataclasses import dataclass, field
from typing import Optional, Literal
from enum import Enum
from pathlib import Path
import time


class MessageType(str, Enum):
    """Message types for team communication"""
    TASK = "task"
    MESSAGE = "message"
    BROADCAST = "broadcast"
    STATUS = "status"
    RESULT = "result"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    WARNING = "warning"
    PLAN_APPROVAL = "plan_approval"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


class TeammateStatus(str, Enum):
    """Teammate lifecycle states"""
    INITIAL = "initial"
    WORKING = "working"
    IDLE = "idle"
    SHUTDOWN = "shutdown"
    DONE = "done"


class MonitorState(str, Enum):
    """Monitor states for timeout detection"""
    NORMAL = "normal"
    WARN_1 = "warn_1"
    WARN_2 = "warn_2"
    WARN_3 = "warn_3"
    DEGRADED = "degraded"


@dataclass
class TeamConfig:
    """Team configuration"""
    team_name: str
    created_at: float = field(default_factory=time.time)
    status: str = "running"
    members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "team_name": self.team_name,
            "created_at": self.created_at,
            "status": self.status,
            "members": self.members,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamConfig":
        return cls(
            team_name=data["team_name"],
            created_at=data.get("created_at", time.time()),
            status=data.get("status", "running"),
            members=data.get("members", []),
        )


@dataclass
class TeammateConfig:
    """Teammate configuration"""
    name: str
    role: str
    task: str
    tools: list[str] = field(default_factory=list)
    status: str = TeammateStatus.INITIAL.value
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    team_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "task": self.task,
            "tools": self.tools,
            "status": self.status,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "team_name": self.team_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeammateConfig":
        return cls(
            name=data["name"],
            role=data["role"],
            task=data["task"],
            tools=data.get("tools", []),
            status=data.get("status", TeammateStatus.INITIAL.value),
            created_at=data.get("created_at", time.time()),
            last_active=data.get("last_active", time.time()),
            team_name=data.get("team_name"),
        )


@dataclass
class Message:
    """Message for team communication"""
    type: str
    from_: str
    to: str
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "from": self.from_,
            "to": self.to,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            type=data["type"],
            from_=data["from"],
            to=data["to"],
            content=data["content"],
            timestamp=data.get("timestamp", time.time()),
            metadata=data.get("metadata", {}),
        )


@dataclass
class StatusReport:
    """Status report from teammate to lead"""
    progress: int = 0
    current_action: str = ""
    completed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_content(self) -> str:
        return f"""进度: {self.progress}%
当前: {self.current_action}
已完成: {', '.join(self.completed) if self.completed else '无'}
剩余: {', '.join(self.remaining) if self.remaining else '无'}
阻塞: {', '.join(self.blockers) if self.blockers else '无'}"""


@dataclass
class MemberMonitorState:
    """Monitor state for a single teammate"""
    member_name: str
    last_status_time: float = field(default_factory=time.time)
    last_status_content: str = ""
    warning_count: int = 0
    progress: int = 0
    current_action: str = ""
    state: str = MonitorState.NORMAL.value
    completed_items: list[str] = field(default_factory=list)
    remaining_items: list[str] = field(default_factory=list)

    def update_from_report(self, report: StatusReport):
        """Update state from status report"""
        self.last_status_time = time.time()
        self.last_status_content = report.to_content()
        self.progress = report.progress
        self.current_action = report.current_action
        self.completed_items = report.completed
        self.remaining_items = report.remaining
        self.warning_count = 0
        self.state = MonitorState.NORMAL.value

    def is_activity_timeout(self, timeout_seconds: float) -> bool:
        """Check if activity timeout occurred"""
        return (time.time() - self.last_status_time) > timeout_seconds

    def is_response_timeout(self, timeout_seconds: float) -> bool:
        """Check if response timeout occurred (after a warning)"""
        if self.state == MonitorState.NORMAL.value:
            return False
        return (time.time() - self.last_status_time) > timeout_seconds

    def should_degrade(self) -> bool:
        """Check if should initiate degradation"""
        return self.warning_count >= 3


@dataclass
class ShutdownRequest:
    """Shutdown request tracking with request_id correlation"""
    request_id: str
    team_name: str
    target: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "team_name": self.team_name,
            "target": self.target,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ShutdownRequest":
        return cls(
            request_id=data["request_id"],
            team_name=data["team_name"],
            target=data["target"],
            status=data.get("status", "pending"),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class PlanRequest:
    """Plan approval request tracking with request_id correlation"""
    request_id: str
    team_name: str
    from_: str
    plan: str
    status: str = "pending"
    feedback: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "team_name": self.team_name,
            "from": self.from_,
            "plan": self.plan,
            "status": self.status,
            "feedback": self.feedback,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanRequest":
        return cls(
            request_id=data["request_id"],
            team_name=data["team_name"],
            from_=data["from"],
            plan=data["plan"],
            status=data.get("status", "pending"),
            feedback=data.get("feedback", ""),
            created_at=data.get("created_at", time.time()),
        )
