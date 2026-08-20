"""Todo工具模块

提供TaskItem/TodoManager（任务列表管理）与TodoTool（LLM工具封装）。
"""
from dataclasses import dataclass
from typing import Any, List

from .registry import Tool


@dataclass
class TaskItem:
    """任务项：id + 描述文本 + 状态(pending/in_progress/completed)"""

    id: str
    text: str
    status: str = "pending"


class TodoManager:
    """任务列表管理器 - 类似 TodoWrite 的 TodoManager"""

    def __init__(self):
        self.items: List[TaskItem] = []

    def update(self, items: List[dict]) -> str:
        """更新任务列表，带严格验证

        验证规则:
        1. 最多20个任务
        2. 同时只能1个 in_progress
        3. 状态必须有效 (pending/in_progress/completed)
        4. 文本不能为空

        @param items: 任务列表，每项包含 id, text, status
        @return: 渲染后的任务列表字符串
        @raises ValueError: 验证失败时
        """
        if len(items) > 20:
            raise ValueError("Max 20 todos allowed")

        validated: List[TaskItem] = []
        in_progress_count = 0

        for i, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", "pending")).lower()
            item_id = str(item.get("id", str(i + 1)))

            if not text:
                raise ValueError(f"Item {item_id}: text required")

            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {item_id}: invalid status '{status}'")

            if status == "in_progress":
                in_progress_count += 1

            validated.append(TaskItem(id=item_id, text=text, status=status))

        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")

        self.items = validated
        return self.render()

    def render(self) -> str:
        """渲染任务列表为可读格式

        @return: 格式如:
          [ ] #1: 任务描述
          [>] #2: 任务描述 (当前执行)
          [x] #3: 任务描述 (已完成)
          (1/3 completed)
        """
        if not self.items:
            return "No todos."

        lines = []
        for item in self.items:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item.status]
            lines.append(f"{marker} #{item.id}: {item.text}")

        done = sum(1 for t in self.items if t.status == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")

        return "\n".join(lines)


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
