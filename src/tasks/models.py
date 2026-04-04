"""Task 数据模型 - 任务项数据结构

包含任务的所有属性和依赖关系信息。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Task:
    """任务数据模型

    用于表示一个任务项，包含任务信息、状态和依赖关系。

    Attributes:
        id: 任务唯一标识符（自增整数）
        subject: 任务主题/标题
        description: 任务详细描述（可选）
        status: 任务状态，取值范围:
            - pending: 待执行
            - in_progress: 执行中
            - completed: 已完成
        blocked_by: 阻塞此任务的任务 ID 列表
        blocks: 被此任务阻塞的任务 ID 列表
        owner: 任务负责人（可选）
        created_at: 创建时间戳
    """

    id: int
    subject: str
    description: str = ""
    status: str = "pending"
    blocked_by: list[int] = field(default_factory=list)
    blocks: list[int] = field(default_factory=list)
    owner: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        """转换为字典格式

        @return: 任务数据字典
        """
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "blocks": self.blocks,
            "owner": self.owner,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从字典创建 Task 实例

        @param data: 任务数据字典
        @return: Task 实例
        """
        return cls(
            id=data.get("id", 0),
            subject=data.get("subject", ""),
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            blocked_by=data.get("blocked_by", []),
            blocks=data.get("blocks", []),
            owner=data.get("owner", ""),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )

    def is_blocked(self) -> bool:
        """检查任务是否被阻塞

        @return: 如果 blocked_by 不为空则返回 True
        """
        return len(self.blocked_by) > 0

    def is_ready(self) -> bool:
        """检查任务是否就绪（未被阻塞且未完成）

        @return: 如果任务可以执行则返回 True
        """
        return not self.is_blocked() and self.status == "pending"
