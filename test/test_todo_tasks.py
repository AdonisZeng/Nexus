"""Todo manager validation rules and tasks-mode data models."""
import pytest

from src.tools.todo import TodoManager, TaskItem
from src.tasks.models import Task as TasksModeTask


class TestTodoManager:
    def test_update_and_render(self):
        mgr = TodoManager()
        out = mgr.update([
            {"id": "1", "text": "do a", "status": "completed"},
            {"id": "2", "text": "do b", "status": "in_progress"},
            {"id": "3", "text": "do c", "status": "pending"},
        ])
        assert "[x] #1: do a" in out
        assert "[>] #2: do b" in out
        assert "[ ] #3: do c" in out
        assert "(1/3 completed)" in out

    def test_render_empty(self):
        assert TodoManager().render() == "No todos."

    def test_max_20_items(self):
        mgr = TodoManager()
        items = [{"id": str(i), "text": f"t{i}", "status": "pending"} for i in range(21)]
        with pytest.raises(ValueError, match="Max 20"):
            mgr.update(items)

    def test_only_one_in_progress(self):
        mgr = TodoManager()
        with pytest.raises(ValueError, match="in_progress"):
            mgr.update([
                {"id": "1", "text": "a", "status": "in_progress"},
                {"id": "2", "text": "b", "status": "in_progress"},
            ])

    def test_invalid_status(self):
        mgr = TodoManager()
        with pytest.raises(ValueError, match="invalid status"):
            mgr.update([{"id": "1", "text": "a", "status": "done"}])

    def test_empty_text_rejected(self):
        mgr = TodoManager()
        with pytest.raises(ValueError, match="text required"):
            mgr.update([{"id": "1", "text": "   ", "status": "pending"}])

    def test_default_status_and_id(self):
        mgr = TodoManager()
        mgr.update([{"text": "a"}])
        assert mgr.items[0].id == "1"
        assert mgr.items[0].status == "pending"

    def test_task_item_model(self):
        item = TaskItem(id="x", text="t")
        assert item.status == "pending"


class TestTasksModeModels:
    def test_task_roundtrip(self):
        task = TasksModeTask(id=1, subject="build", description="d", blocked_by=[2])
        restored = TasksModeTask.from_dict(task.to_dict())
        assert restored.id == 1
        assert restored.subject == "build"
        assert restored.blocked_by == [2]
        assert restored.status == "pending"

    def test_is_blocked_and_ready(self):
        blocked = TasksModeTask(id=1, subject="a", blocked_by=[0])
        ready = TasksModeTask(id=2, subject="b")
        assert blocked.is_blocked() is True
        assert blocked.is_ready() is False
        assert ready.is_ready() is True

    def test_completed_not_ready(self):
        done = TasksModeTask(id=1, subject="a", status="completed")
        assert done.is_ready() is False
