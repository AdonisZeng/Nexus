"""Team: models roundtrip, file storage and task board (tmp dirs, no subprocess)."""
import pytest

from src.team.models import TeamConfig, TeammateConfig, Message, TeammateStatus
from src.team.storage import TeamStorage
from src.team.task_board import TaskBoard, Task, TaskStatus


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(TeamStorage, "BASE_DIR", tmp_path / "teams")
    return TeamStorage()


@pytest.fixture
def board(tmp_path):
    return TaskBoard("alpha", base_dir=tmp_path / "board")


class TestModels:
    def test_team_config_roundtrip(self):
        cfg = TeamConfig(team_name="alpha", members=["a", "b"])
        restored = TeamConfig.from_dict(cfg.to_dict())
        assert restored.team_name == "alpha"
        assert restored.members == ["a", "b"]
        assert restored.status == "running"

    def test_teammate_config_roundtrip(self):
        cfg = TeammateConfig(name="worker", role="coder", task="build", tools=["file_read"], team_name="alpha")
        restored = TeammateConfig.from_dict(cfg.to_dict())
        assert restored.name == "worker"
        assert restored.tools == ["file_read"]
        assert restored.team_name == "alpha"

    def test_message_roundtrip(self):
        msg = Message(type="task", from_="leader", to="worker", content="start")
        restored = Message.from_dict(msg.to_dict())
        assert restored.from_ == "leader"
        assert restored.content == "start"


class TestTeamStorage:
    def test_save_load_team_config(self, storage):
        storage.save_team_config(TeamConfig(team_name="alpha", members=["m1"]))
        loaded = storage.load_team_config("alpha")
        assert loaded is not None
        assert loaded.members == ["m1"]

    def test_load_missing_team_config(self, storage):
        assert storage.load_team_config("ghost") is None

    def test_member_config_requires_team_name(self, storage):
        with pytest.raises(ValueError):
            storage.save_member_config(TeammateConfig(name="w", role="r", task="t"))

    def test_save_load_member_config(self, storage):
        cfg = TeammateConfig(name="w1", role="coder", task="build", team_name="alpha")
        storage.save_member_config(cfg)
        loaded = storage.load_member_config("alpha", "w1")
        assert loaded is not None and loaded.role == "coder"
        assert storage.list_members("alpha") == ["w1"]

    def test_list_teams_and_delete(self, storage):
        storage.save_team_config(TeamConfig(team_name="alpha"))
        storage.save_team_config(TeamConfig(team_name="beta"))
        assert set(storage.list_teams()) == {"alpha", "beta"}
        storage.delete_team("beta")
        assert storage.list_teams() == ["alpha"]

    def test_team_spec_roundtrip(self, storage):
        storage.save_team_spec("alpha", "# design\nbuild it")
        assert storage.get_team_spec("alpha") == "# design\nbuild it"
        assert storage.get_team_spec("ghost") is None

    def test_team_todo_roundtrip(self, storage):
        todo = storage.create_team_todo("alpha")
        assert todo["current_step"] == 2
        loaded = storage.load_team_todo("alpha")
        assert loaded["team_name"] == "alpha"
        advanced = storage.advance_todo_step("alpha")
        assert advanced["current_step"] == 3
        # step 2 completed, step 3 in progress
        statuses = {s["id"]: s["status"] for s in advanced["steps"]}
        assert statuses[2] == "completed"
        assert statuses[3] == "in_progress"

    def test_inbox_append_and_clear(self, storage):
        msg = Message(type="task", from_="leader", to="w1", content="hello")
        storage.append_to_inbox("alpha", "w1", msg)
        messages = storage.read_and_clear_inbox("alpha", "w1")
        assert len(messages) == 1
        assert messages[0].content == "hello"
        # inbox cleared after read
        assert storage.read_and_clear_inbox("alpha", "w1") == []


class TestTaskBoard:
    def test_add_task_assigns_incrementing_ids(self, board):
        t1 = board.add_task("first")
        t2 = board.add_task("second", description="desc")
        assert (t1.id, t2.id) == (1, 2)
        assert t2.description == "desc"
        assert t1.status == TaskStatus.PENDING.value

    def test_get_task_and_missing(self, board):
        board.add_task("first")
        assert board.get_task(1).subject == "first"
        assert board.get_task(99) is None

    def test_get_all_tasks_sorted(self, board):
        board.add_task("a")
        board.add_task("b")
        board.add_task("c")
        assert [t.subject for t in board.get_all_tasks()] == ["a", "b", "c"]

    def test_ids_survive_reload(self, tmp_path):
        base = tmp_path / "board"
        TaskBoard("alpha", base_dir=base).add_task("first")
        board2 = TaskBoard("alpha", base_dir=base)
        assert board2.add_task("second").id == 2

    def test_claim_and_complete(self, board):
        board.add_task("work")
        assert board.claim(1, "worker-a") is True
        task = board.get_task(1)
        assert task.owner == "worker-a"
        assert task.status == TaskStatus.IN_PROGRESS.value
        # cannot claim again
        assert board.claim(1, "worker-b") is False
        assert board.complete(1) is True
        assert board.get_task(1).status == TaskStatus.COMPLETED.value

    def test_release_back_to_pending(self, board):
        board.add_task("work")
        board.claim(1, "worker-a")
        assert board.release(1) is True
        task = board.get_task(1)
        assert task.status == TaskStatus.PENDING.value
        assert task.owner is None

    def test_scan_unclaimed_skips_blocked(self, board):
        board.add_task("base")
        board.add_task("child", blocked_by=[1])
        assert [t.id for t in board.scan_unclaimed()] == [1]
        board.complete(1)
        assert [t.id for t in board.scan_unclaimed()] == [2]

    def test_claim_blocked_task_fails(self, board):
        board.add_task("base")
        board.add_task("child", blocked_by=[1])
        assert board.claim(2, "worker-a") is False

    def test_scan_and_claim(self, board):
        board.add_task("only")
        claimed = board.scan_and_claim("worker-a")
        assert claimed is not None and claimed.id == 1
        assert board.scan_and_claim("worker-b") is None

    def test_blocker_status(self, board):
        board.add_task("base")
        board.add_task("child", blocked_by=[1])
        status = board.get_blocker_status(2)
        assert status["can_proceed"] is False
        assert status["blockers"][0]["id"] == 1
        board.complete(1)
        assert board.get_blocker_status(2)["can_proceed"] is True

    def test_get_status_counts(self, board):
        board.add_task("a")
        board.add_task("b")
        board.claim(1, "w")
        board.complete(2)
        status = board.get_status()
        assert status["total"] == 2
        assert status["in_progress"] == 1
        assert status["completed"] == 1
        assert "Task Board: alpha" in board.format_status()
