"""Adapter layer: registry, formatter, prompt-injection fallback, ChatResult."""
import pytest

from src.adapters.registry import AdapterRegistry
from src.adapters.formatter import MessageFormatter, decode_html_entities
from src.adapters.base import ModelAdapter, ChatResult, StreamEventType

EXPECTED_PROVIDERS = {
    "anthropic", "openai", "ollama", "lmstudio", "xai", "custom", "minimax",
}

# XML closing tag built via concatenation to keep this file tooling-safe
CLOSE = "</" + "tool_call>"


@pytest.fixture(scope="module", autouse=True)
def _register_adapters():
    AdapterRegistry.create_all_registered()


class TestRegistry:
    def test_all_providers_registered(self):
        providers = set(AdapterRegistry.list_providers())
        assert EXPECTED_PROVIDERS <= providers

    def test_get_unknown_returns_none(self):
        assert AdapterRegistry.get("not-a-provider") is None

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            AdapterRegistry.create("not-a-provider", {})

    def test_create_ollama_from_config(self):
        adapter = AdapterRegistry.create(
            "ollama", {"ollama": {"model": "llama3", "url": "http://localhost:11434"}}
        )
        assert adapter.get_name() == "ollama"
        assert adapter.model == "llama3"

    def test_create_lmstudio_from_config(self):
        adapter = AdapterRegistry.create(
            "lmstudio", {"lmstudio": {"model": "qwen2.5"}}
        )
        assert adapter.model == "qwen2.5"

    def test_self_registration_via_subclass(self):
        class _TmpAdapter(ModelAdapter):
            PROVIDER_NAME = "_tmp_test_provider"

            async def chat(self, messages, system_prompt=None):
                return ""

            async def chat_with_tools(self, messages, tools, system_prompt=None):
                return "", []

            def get_name(self):
                return "_tmp"

            def supports_streaming(self):
                return False

        try:
            assert AdapterRegistry.get("_tmp_test_provider") is _TmpAdapter
        finally:
            AdapterRegistry._adapters.pop("_tmp_test_provider", None)


class TestChatResult:
    async def test_default_stop_reason_delegates(self):
        from conftest import FakeAdapter

        class FakeSub(ModelAdapter):
            PROVIDER_NAME = None

            def __init__(self):
                super().__init__(model="m")
                self._inner = FakeAdapter(["hello"])

            async def chat(self, messages, system_prompt=None):
                return await self._inner.chat(messages, system_prompt)

            async def chat_with_tools(self, messages, tools, system_prompt=None):
                return await self._inner.chat_with_tools(messages, tools, system_prompt)

            def get_name(self):
                return "fakesub"

            def supports_streaming(self):
                return False

        adapter = FakeSub()
        result = await adapter.chat_with_tools_and_stop_reason([], [])
        assert isinstance(result, ChatResult)
        assert result.text == "hello"
        assert result.tool_calls == []
        assert result.stop_reason is None

    async def test_chat_stream_default_emits_events(self):
        from src.adapters.ollama import OllamaAdapter

        adapter = OllamaAdapter(model="llama3")

        async def fake_chat_with_tools(messages, tools, system_prompt=None):
            return "hi", [{"name": "t", "arguments": {}, "id": "1"}]

        adapter.chat_with_tools = fake_chat_with_tools
        events = [e async for e in adapter.chat_stream([], [])]
        types = [e.type for e in events]
        assert StreamEventType.TEXT_DELTA in types
        assert StreamEventType.TOOL_USE_COMPLETE in types
        assert types[-1] == StreamEventType.MESSAGE_STOP


class TestFormatter:
    def test_to_openai_system_prompt_and_roles(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "skip me"},
            {"role": "assistant", "content": "yo"},
        ]
        out = MessageFormatter.to_openai(msgs, system_prompt="sys")
        assert out[0] == {"role": "system", "content": "sys"}
        assert [m["role"] for m in out] == ["system", "user", "assistant"]

    def test_to_openai_developer_role(self):
        out = MessageFormatter.to_openai([], system_prompt="sys", supports_developer_role=True)
        assert out[0]["role"] == "developer"

    def test_to_openai_tool_calls_serialized(self):
        msgs = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "file_read", "arguments": {"path": "a"}}],
        }]
        out = MessageFormatter.to_openai(msgs)
        tc = out[0]["tool_calls"][0]
        assert tc["id"] == "c1"
        assert tc["function"]["name"] == "file_read"
        assert tc["function"]["arguments"] == '{"path": "a"}'

    def test_to_openai_tool_result(self):
        msgs = [{"role": "tool", "content": "res", "tool_call_id": "c1"}]
        out = MessageFormatter.to_openai(msgs)
        assert out[0] == {"role": "tool", "content": "res", "tool_call_id": "c1"}

    def test_to_anthropic_tool_roundtrip(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "think",
             "tool_calls": [{"id": "c1", "name": "t", "arguments": {"a": 1}}]},
            {"role": "tool", "content": "ok", "tool_call_id": "c1"},
        ]
        system, out = MessageFormatter.to_anthropic(msgs)
        assert system == "sys"
        assert out[0]["role"] == "assistant"
        blocks = out[0]["content"]
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "tool_use" and blocks[1]["input"] == {"a": 1}
        assert out[1]["role"] == "user"
        assert out[1]["content"][0]["type"] == "tool_result"

    def test_to_ollama_drops_system_in_messages(self):
        msgs = [
            {"role": "system", "content": "x"},
            {"role": "user", "content": "hi"},
        ]
        out = MessageFormatter.to_ollama(msgs, system_prompt="sys")
        assert out[0] == {"role": "system", "content": "sys"}
        assert len(out) == 2

    def test_decode_html_entities(self):
        assert decode_html_entities("&quot;a&quot; &amp; &#39;b&#39;") == '"a" & \'b\''
        assert decode_html_entities("&lt;x&gt;") == "<x>"
        assert decode_html_entities("&#x41;&#66;") == "AB"
        assert decode_html_entities("no entities") == "no entities"


class TestPromptInjectionFallback:
    """The XML tool-call fallback duplicated across adapters (ollama as representative)."""

    @pytest.fixture
    def adapter(self):
        from src.adapters.ollama import OllamaAdapter
        return OllamaAdapter(model="llama3")

    def test_build_tool_prompt_lists_tools(self, adapter):
        tools = [{
            "name": "file_read",
            "description": "Read a file",
            "input_schema": {
                "properties": {"path": {"type": "string", "description": "File path"}},
                "required": ["path"],
            },
        }]
        prompt = adapter._build_tool_prompt(tools)
        assert "file_read" in prompt
        assert "<tool_call" in prompt
        assert "(required)" in prompt

    def test_parse_tool_calls_single(self, adapter):
        response = 'Sure.\n<tool_call name="file_read">\n{"path": "a.txt"}\n' + CLOSE
        calls = adapter._parse_tool_calls_from_response(response)
        assert len(calls) == 1
        assert calls[0]["name"] == "file_read"
        assert calls[0]["arguments"] == {"path": "a.txt"}

    def test_parse_tool_calls_multiple(self, adapter):
        response = (
            '<tool_call name="a">{"x": 1}' + CLOSE +
            '<tool_call name="b">{"y": 2}' + CLOSE
        )
        calls = adapter._parse_tool_calls_from_response(response)
        assert [c["name"] for c in calls] == ["a", "b"]

    def test_parse_tool_calls_malformed_json(self, adapter):
        response = '<tool_call name="a">not json at all' + CLOSE
        calls = adapter._parse_tool_calls_from_response(response)
        assert calls[0]["arguments"] == {"raw": "not json at all"}

    def test_parse_no_tool_calls(self, adapter):
        assert adapter._parse_tool_calls_from_response("plain answer") == []

    async def test_chat_with_tool_prompt_uses_chat(self, adapter):
        captured = {}

        async def fake_chat(messages, system_prompt=None):
            captured["prompt"] = system_prompt
            return '<tool_call name="a">{"x": 1}' + CLOSE

        adapter.chat = fake_chat
        text, calls = await adapter._chat_with_tool_prompt(
            [], [{"name": "a", "input_schema": {}}], "sys"
        )
        assert "sys" in captured["prompt"]
        assert calls[0]["name"] == "a"
