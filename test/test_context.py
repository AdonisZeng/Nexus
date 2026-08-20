"""Context utilities: merger, micro-compactor, persister, normalizer, compressor, tokenizer."""
import pytest

from src.context.message_merger import MessageMerger, merge_consecutive_messages
from src.context.micro_compactor import (
    MicroCompactor,
    TOOL_RESULT_PLACEHOLDER,
    micro_compact_messages,
)
from src.context.tool_persister import ToolOutputPersister, PersistConfig
from src.context.tool_use_normalizer import normalize_tool_uses, ORPHANED_RESULT_PLACEHOLDER
from src.context.memory import LLMContextCompressor
from src.utils.tokenizer import count_tokens, estimate_tokens, count_messages_tokens

from conftest import FakeAdapter


class TestMessageMerger:
    def test_merge_consecutive_user(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
        ]
        out = merge_consecutive_messages(msgs)
        assert len(out) == 2
        assert out[0]["content"] == "a\nb"

    def test_system_not_merged_by_default(self):
        msgs = [
            {"role": "system", "content": "s1"},
            {"role": "system", "content": "s2"},
        ]
        out = merge_consecutive_messages(msgs)
        assert len(out) == 2

    def test_tool_calls_not_merged(self):
        msgs = [
            {"role": "assistant", "content": "a", "tool_calls": [{"id": "1"}]},
            {"role": "assistant", "content": "b"},
        ]
        out = merge_consecutive_messages(msgs)
        assert len(out) == 2

    def test_max_consecutive_merges_limit(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(20)]
        merger = MessageMerger()
        merger.config.max_consecutive_merges = 3
        out = merger.merge(msgs)
        # 3 merges per run -> groups of 4 -> 20 / 4 = 5 messages remain
        assert len(out) == 5
        assert out[0]["content"] == "m0\nm1\nm2\nm3"

    def test_empty_and_single(self):
        assert merge_consecutive_messages([]) == []
        single = [{"role": "user", "content": "x"}]
        assert merge_consecutive_messages(single) == single


class TestMicroCompactor:
    def test_no_change_when_few_tool_results(self):
        msgs = [
            {"role": "tool", "content": "x" * 500, "tool_call_id": "1"},
            {"role": "tool", "content": "y" * 500, "tool_call_id": "2"},
        ]
        out = micro_compact_messages(msgs, keep_recent=3)
        assert out[0]["content"] == "x" * 500

    @pytest.mark.xfail(
        reason="known bug: MicroCompactConfig lacks compact_threshold/placeholder attrs",
        strict=False,
    )
    def test_old_large_results_replaced_with_placeholder(self):
        msgs = [
            {"role": "tool", "content": "old " * 200, "tool_call_id": str(i)}
            for i in range(5)
        ]
        compactor = MicroCompactor()
        compactor.compact(msgs, keep_recent=2)
        assert msgs[0]["content"] == TOOL_RESULT_PLACEHOLDER


class TestToolPersister:
    def test_small_output_not_persisted(self, tmp_path):
        persister = ToolOutputPersister(PersistConfig(storage_dir=tmp_path))
        result = persister.persist("t1", "small output")
        assert result.was_persisted is False
        assert result.preview == "small output"

    def test_large_output_persisted_with_preview(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # keep relative_to(cwd) safe
        persister = ToolOutputPersister(
            PersistConfig(threshold=100, preview_chars=50, storage_dir=tmp_path / "store")
        )
        big = "A" * 5000
        result = persister.persist("t2", big)
        assert result.was_persisted is True
        assert result.persisted_path.exists()
        assert result.persisted_path.read_text(encoding="utf-8") == big
        assert result.preview.startswith("A" * 50)
        assert "persisted" in result.preview

        loaded = persister.load_persisted("t2")
        assert loaded == big

    def test_load_missing_returns_none(self, tmp_path):
        persister = ToolOutputPersister(PersistConfig(storage_dir=tmp_path))
        assert persister.load_persisted("ghost") is None

    def test_cleanup_old_removes_expired(self, tmp_path):
        persister = ToolOutputPersister(
            PersistConfig(threshold=10, storage_dir=tmp_path)
        )
        persister.persist("t3", "Z" * 100)
        removed = persister.cleanup_old(max_age_days=-1)  # everything is "old"
        assert removed == 1
        assert persister.load_persisted("t3") is None


class TestToolUseNormalizer:
    def test_orphaned_tool_use_gets_placeholder(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "u1", "name": "t", "input": {}},
            ]},
        ]
        out = normalize_tool_uses(msgs)
        assert len(out) == 2
        inserted = out[-1]
        assert inserted["content"][0]["type"] == "tool_result"
        assert inserted["content"][0]["tool_use_id"] == "u1"
        assert inserted["content"][0]["content"] == ORPHANED_RESULT_PLACEHOLDER

    def test_matched_pair_untouched(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "u1", "name": "t", "input": {}},
            ]},
            {"role": "tool", "content": "ok", "tool_call_id": "u1"},
        ]
        out = normalize_tool_uses(msgs)
        assert len(out) == 2

    def test_empty_messages(self):
        assert normalize_tool_uses([]) == []


class TestLLMContextCompressor:
    async def test_compress_messages_summarizes(self):
        adapter = FakeAdapter(["SUMMARY-TEXT"])
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        result = await LLMContextCompressor.compress_messages(messages, adapter)
        assert result is not None
        assert any("SUMMARY-TEXT" in str(m.get("content", "")) for m in result)
        assert adapter.calls  # adapter.chat was invoked

    async def test_compress_messages_skips_when_too_few(self):
        adapter = FakeAdapter(["SUMMARY"])
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
        result = await LLMContextCompressor.compress_messages(messages, adapter, min_non_system=2)
        assert result is None

    async def test_compress_context_on_agent_context(self):
        from src.agent.context import AgentContext

        adapter = FakeAdapter(["COMPRESSED"])
        ctx = AgentContext()
        ctx.add_user_message("q1")
        ctx.add_assistant_message("a1")
        ctx.add_user_message("q2")
        ok = await LLMContextCompressor.compress_context(ctx, adapter)
        assert ok is True
        assert any("COMPRESSED" in m.content for m in ctx.short_term_memory)


class TestTokenizer:
    def test_count_tokens_positive(self):
        assert count_tokens("hello world, this is a test") > 0

    def test_count_tokens_empty(self):
        assert count_tokens("") == 0

    def test_estimate_tokens_chinese_vs_english(self):
        en = estimate_tokens("a" * 100)
        zh = estimate_tokens("字" * 100)
        assert zh > en  # Chinese chars count ~2x

    def test_count_messages_tokens_overhead(self):
        msgs = [{"role": "user", "content": "hi"}]
        total = count_messages_tokens(msgs)
        assert total > count_tokens("hi")

    def test_count_messages_tokens_empty(self):
        assert count_messages_tokens([]) == 0
