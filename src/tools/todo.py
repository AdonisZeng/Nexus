"""Todo工具模块

提供TodoTool类，用于LLM调用任务列表管理功能。
"""
from typing import Any

from .registry import Tool
from ..todo.manager import TodoManager


# 全局TodoManager实例
_global_todo_manager: TodoManager | None = None


def get_todo_manager() -> TodoManager:
    """获取全局TodoManager实例

    @return: 全局TodoManager实例
    """
    global _global_todo_manager
    if _global_todo_manager is None:
        _global_todo_manager = TodoManager()
    return _global_todo_manager


class TodoTool(Tool):
    """Todo工具类

    封装TodoManager为可被LLM调用的工具，用于更新和渲染任务列表。
    """

    @property
    def name(self) -> str:
        """工具名称"""
        return "todo"

    @property
    def description(self) -> str:
        """工具描述

        用于创建和管理任务列表的工具。接受任务项数组，每个任务项包含:
        - id: 任务唯一标识符
        - text: 任务描述文本
        - status: 任务状态 (pending/in_progress/completed)

        返回渲染后的任务列表视图。
        """
        return "更新和管理任务列表的工具，用于跟踪多步骤任务的进度"

    @property
    def is_mutating(self) -> bool:
        """该工具会修改状态"""
        return True

    @property
    def requires_approval(self) -> bool:
        """需要用户批准"""
        return False

    def _get_input_schema(self) -> dict:
        """获取输入模式

        @return: 输入schema字典
        """
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"]
                            }
                        },
                        "required": ["id", "text", "status"]
                    }
                }
            },
            "required": ["items"]
        }

    async def execute(self, **kwargs) -> Any:
        """执行工具

        @param kwargs: 包含items参数的任务列表
        @return: 渲染后的任务列表字符串
        """
        items = kwargs.get("items", [])
        todo_manager = get_todo_manager()
        todo_manager.update(items)
        return todo_manager.render()
