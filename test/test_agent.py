"""Agent core: LoopDetector, AgentContext, AgentLoop run flows."""
import pytest

from src.agent.session import LoopDetector
from src.agent.context import AgentContext, ConversationState, ContextMessage
from src.agent.loop import AgentLoop, IdleException
from src.agent.work_item import WorkItem, WorkItemSource


class TestLoopDetector:
    def test_no_loop_initially(self):
        d = LoopDetector()
        is_loop, reason = d.detect_loop()
        assert is_loop is False and reason == ""

    def test_repeated_tool_call_detected(self):
        d = LoopDetector()
        for _ in range(5):
            d.record_tool_call("file_read", {"path": "a.txt"})
        is_loop, reason = d.detect_loop()
        assert is_loop is True
        assert "file_read" in reason

    def test_varied_tool_calls_not_detected(self):
        d = LoopDetector()
        for i in range(5):
            d.record_tool_call("file_read", {"path": f"file_{i}.txt"})
        is_loop, _ = d.detect_loop()
        assert is_loop is False

    def test_repeated_output_detected(self):
        d = LoopDetector()
        for _ in range(5):
            d.record_output("the exact same long output text here")
        is_loop, reason = d.detect_loop()
        assert is_loop is True
        assert "输出" in reason

    def test_history_capped(self):
        d = LoopDetector()
        for i in range(50):
            d.record_tool_call("t", {"i": i})
        assert len(d._tool_call_history) <= d.max_history


class TestConversationState:
    def test_increment_to_max_terminates(self):
        state = ConversationState(max_iterations=2)
        state.increment_iteration()
        assert state.should_terminate is False
        state.increment_iteration()
        assert state.should_terminate is True
        assert state.is_finished

    def test_mark_finished(self):
        state = ConversationState()
        state.mark_finished("done")
        assert state.status == "finished"
        assert state.is_finished

    def test_mark_error(self):
        state = ConversationState()
        state.mark_error("boom")
        assert state.status == "error"
        assert state.termination_reason == "boom"


class TestAgentContext:
    def test_add_message_tracks_tokens(self):
        ctx = AgentContext()
        ctx.add_user_message("hello", token_count=10)
        ctx.add_assistant_message("world", token_count=5)
        assert len(ctx) == 2
        assert ctx.total_tokens_used == 15
        assert ctx.messages[0] == {"role": "user", "content": "hello"}

    def test_should_compress_threshold(self):
        ctx = AgentContext(max_context_window=1000, compress_threshold=0.7)
        assert ctx.should_compress(current_tokens=600) is False
        assert ctx.should_compress(current_tokens=700) is True

    def test_compression_ratio(self):
        ctx = AgentContext(max_context_window=1000)
        assert ctx.get_compression_ratio(current_tokens=250) == pytest.approx(0.25)

    def test_calculate_total_tokens_from_messages(self):
        ctx = AgentContext()
        total = ctx.calculate_total_tokens([{"role": "user", "content": "hi there"}])
        assert total > 0

    def test_clear_resets(self):
        ctx = AgentContext()
        ctx.add_user_message("x", token_count=3)
        ctx.clear()
        assert len(ctx) == 0 and ctx.total_tokens_used == 0


class _QueueSource(WorkItemSource):
    def __init__(self, items):
        self._items = list(items)
        self.completed = []

    async def get_next_work_item(self):
        return self._items.pop(0) if self._items else None

    async def on_work_item_completed(self, item, result):
        self.completed.append((item.id, result))

    def has_more_work(self):
        return bool(self._items)


class TestAgentLoop:
    async def test_run_simple_completion(self):
        loop = AgentLoop(max_iterations=3)

        async def execute_fn():
            return "final answer", []

        result = await loop.run(execute_fn)
        assert result == "final answer"
        assert loop.context.state.is_finished

    async def test_run_with_tool_call_continues(self):
        loop = AgentLoop(max_iterations=5)
        steps = {"n": 0}

        async def execute_fn():
            steps["n"] += 1
            if steps["n"] == 1:
                return "calling tool", [{"name": "t", "arguments": {}, "id": "1"}]
            return "done", []

        result = await loop.run(execute_fn)
        assert result == "done"
        assert steps["n"] == 2

    async def test_run_max_iterations_exits(self):
        loop = AgentLoop(max_iterations=2)

        async def execute_fn():
            return "again", [{"name": "t", "arguments": {}, "id": "1"}]

        await loop.run(execute_fn)
        assert loop.context.state.is_finished

    async def test_idle_exception_exits_gracefully(self):
        loop = AgentLoop(max_iterations=3)

        async def execute_fn():
            raise IdleException("nothing to do")

        result = await loop.run(execute_fn)
        assert result == ""
        assert loop.idle_requested is True

    async def test_work_item_source_flow(self):
        source = _QueueSource([
            WorkItem(id="w1", description="first task"),
        ])
        loop = AgentLoop(max_iterations=3, work_item_source=source)

        async def execute_fn():
            return "processed", []

        result = await loop.run(execute_fn)
        assert result == "processed"
        assert [c[0] for c in source.completed] == ["w1"]

    async def test_confirmation_check_true_continues_until_source_empty(self):
        source = _QueueSource([
            WorkItem(id="w1", description="a"),
            WorkItem(id="w2", description="b"),
        ])
        loop = AgentLoop(max_iterations=6, work_item_source=source)

        async def _true(resp, stop):
            return True

        loop._on_confirmation_check = _true
        seen = []

        async def execute_fn():
            seen.append(1)
            return f"resp-{len(seen)}", []

        result = await loop.run(execute_fn)
        # both work items completed via confirmation
        assert [c[0] for c in source.completed] == ["w1", "w2"]

    def test_get_status_shape(self):
        loop = AgentLoop(max_iterations=2)
        status = loop.get_status()
        assert status["status"] == "active"
        assert status["max_iterations"] == 2
        assert "metrics" in status
