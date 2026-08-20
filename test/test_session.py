"""AgentSession.execute_task end-to-end flows driven by FakeAdapter."""
import pytest

from src.agent.session import AgentSession
from src.agent import EventType

from conftest import FakeAdapter


def _events(gen):
    """Collect (type, content) pairs from the async generator."""

    async def collect():
        out = []
        async for ev in gen:
            out.append(ev)
        return out

    return collect()


class TestExecuteTask:
    async def test_plain_response(self, tmp_path):
        adapter = FakeAdapter(["hello there"])
        session = AgentSession(adapter, cwd=str(tmp_path))
        events = await _events(session.execute_task("hi"))

        outputs = [e.content for e in events if e.type == EventType.OUTPUT]
        assert any("hello there" in c for c in outputs)
        assert session.messages[-1] == {"role": "assistant", "content": "hello there"}

    async def test_tool_loop_executes_real_tool(self, tmp_path):
        (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
        adapter = FakeAdapter([
            ("let me list", [{
                "name": "list_dir",
                "arguments": {"dir_path": str(tmp_path)},
                "id": "call_1",
            }]),
            "all done",
        ])
        session = AgentSession(adapter, cwd=str(tmp_path))
        events = await _events(session.execute_task("list it"))

        types = [e.type for e in events]
        assert EventType.TOOL_CALL in types
        assert EventType.TOOL_RESULT in types
        tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
        assert any("marker.txt" in e.content for e in tool_results)
        # conversation ended with the final assistant answer
        assert session.messages[-1]["content"] == "all done"

    async def test_repetitive_loop_breaks(self, tmp_path):
        same_call = ("again", [{
            "name": "list_dir",
            "arguments": {"dir_path": str(tmp_path)},
            "id": "call_x",
        }])
        adapter = FakeAdapter([same_call] * 8)
        session = AgentSession(adapter, cwd=str(tmp_path))
        events = await _events(session.execute_task("loop"))

        contents = [e.content for e in events]
        assert any("循环" in c for c in contents)
        assert any("中断" in c for c in contents)

    async def test_nag_reminder_injected(self, tmp_path):
        adapter = FakeAdapter([
            ("working", [{
                "name": "list_dir",
                "arguments": {"dir_path": str(tmp_path)},
                "id": "call_2",
            }]),
            "finished",
        ])
        session = AgentSession(adapter, cwd=str(tmp_path))
        session.rounds_since_todo = 3  # one more non-todo round triggers reminder
        await _events(session.execute_task("do it"))

        # reminder is injected during the loop (flag set, counter reset);
        # execute_task strips it from history at the end
        assert session._reminder_injected is True
        assert session.rounds_since_todo == 0
        assert not any(
            m.get("role") == "system" and "<reminder>" in str(m.get("content", ""))
            for m in session.messages
        )

    async def test_todo_call_resets_nag_counter(self, tmp_path):
        adapter = FakeAdapter([
            ("todo first", [{
                "name": "todo",
                "arguments": {"items": [{"id": "1", "text": "a", "status": "in_progress"}]},
                "id": "call_3",
            }]),
            "finished",
        ])
        session = AgentSession(adapter, cwd=str(tmp_path))
        session.rounds_since_todo = 2
        await _events(session.execute_task("plan it"))
        assert session.rounds_since_todo == 0

    async def test_context_compression_triggered(self, tmp_path):
        adapter = FakeAdapter(["ok"])
        session = AgentSession(adapter, cwd=str(tmp_path))
        # Pre-fill history above the 70% of 200K-token threshold (~140K tokens)
        huge = "word " * 200000  # ~200K tokens
        session.messages = [
            {"role": "user", "content": huge},
            {"role": "assistant", "content": "ack"},
        ]

        compressed = {"called": False}

        async def fake_compress():
            compressed["called"] = True

        session._compress_context_llm = fake_compress
        await _events(session.execute_task("next question"))
        assert compressed["called"] is True
