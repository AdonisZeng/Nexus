"""Shared test fixtures: project path setup, hermetic HOME, FakeAdapter."""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def hermetic_home(tmp_path, monkeypatch):
    """Redirect HOME to tmp so tests never touch the real ~/.nexus."""
    home = str(tmp_path / "home")
    Path(home).mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    yield tmp_path


class FakeAdapter:
    """Scripted model adapter: consumes queued responses for each call.

    Queue entries:
      - str                     -> chat text / (text, []) for tool calls
      - (text, tool_calls)      -> response with tool calls
      - Exception               -> raised
    """

    def __init__(self, responses=None, stop_reason="end_turn"):
        self.responses = list(responses or [])
        self.stop_reason = stop_reason
        self.calls = []
        self.model = "fake-model"

    def _next(self):
        if not self.responses:
            return ""
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat(self, messages, system_prompt=None):
        self.calls.append(("chat", messages, system_prompt))
        item = self._next()
        return item if isinstance(item, str) else item[0]

    async def chat_with_tools(self, messages, tools, system_prompt=None):
        self.calls.append(("chat_with_tools", messages, tools))
        item = self._next()
        if isinstance(item, tuple):
            return item
        return item, []

    async def chat_with_tools_and_stop_reason(self, messages, tools, system_prompt=None):
        from src.adapters.base import ChatResult

        text, tool_calls = await self.chat_with_tools(messages, tools, system_prompt)
        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=self.stop_reason)

    def get_name(self):
        return "fake"

    def supports_streaming(self):
        return False

    def get_capabilities(self):
        from src.adapters.capabilities import ModelCapabilities

        return ModelCapabilities()

    async def close(self):
        pass

    @classmethod
    def from_config(cls, config: dict):
        return cls()


@pytest.fixture
def fake_adapter():
    return FakeAdapter()
