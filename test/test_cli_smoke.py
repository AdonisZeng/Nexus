"""NexusCLI.initialize() smoke test driven by FakeAdapter (no network)."""
import asyncio

import pytest

from src.cli.main import NexusCLI

from conftest import FakeAdapter


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "models": {"default": "fake", "fake": {}},
        "mcp": {},
    }
    return NexusCLI(config, config_path=str(tmp_path / "config.yaml"))


async def _initialize(cli, monkeypatch):
    """Run initialize() with a FakeAdapter and clean up the MCP task."""
    monkeypatch.setattr(NexusCLI, "_create_model_adapter", lambda self, cfg: FakeAdapter())
    await cli.initialize()
    task = getattr(cli, "_mcp_connection_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def test_initialize_wires_everything(cli, monkeypatch):
    await _initialize(cli, monkeypatch)

    # adapter + session wired
    assert cli.model_adapter is not None
    assert cli.model_adapter.get_name() == "fake"
    assert cli.session_id
    assert cli.messages == []

    # tool registry fully populated
    tools = cli.tool_registry.list_tools()
    for expected in ("file_read", "file_write", "list_dir", "shell_run", "todo"):
        assert expected in tools

    # orchestrator present for tool execution
    assert cli.tool_orchestrator is not None

    # system prompt built (tools + commands sections)
    prompt = cli.system_prompt
    assert prompt
    assert "file_read" in prompt
    assert "/help" in prompt or "可用命令" in prompt


async def test_initialize_with_empty_config(cli, monkeypatch, tmp_path):
    # minimal config still boots
    cli.config = {"models": {}}
    await _initialize(cli, monkeypatch)
    assert cli.model_adapter.get_name() == "fake"
