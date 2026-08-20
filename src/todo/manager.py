"""TodoManager - 任务列表管理器"""
from typing import List
from .models import TaskItem


class TodoManager:
    """任务列表管理器 - 类似 TodoWrite 的 TodoManager"""

    def __init__(self):
        self.items: List[TaskItem] = []

    def update(self, items: List[dict]) -> str:
        """
        更新任务列表，带严格验证

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
        """
        渲染任务列表为可读格式

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
