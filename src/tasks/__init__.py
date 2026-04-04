"""Tasks 模块 - 支持依赖图的持久化任务管理系统

提供任务创建、依赖管理、持久化存储等功能。
"""

from .models import Task
from .manager import TaskManager

__all__ = ["Task", "TaskManager"]
