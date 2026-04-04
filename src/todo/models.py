"""任务模块

提供任务项数据结构和相关类型定义。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskItem:
    """任务项 - 类似 TodoWrite 的任务数据结构

    用于跟踪和管理多步骤任务的状态和进度。

    Attributes:
        id: 任务唯一标识符
        text: 任务描述文本
        status: 任务状态，取值范围:
            - "pending": 待执行
            - "in_progress": 执行中
            - "completed": 已完成
    """

    id: str
    text: str
    status: str = "pending"
