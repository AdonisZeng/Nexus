"""Command registry integrity and offline command execution."""
import pytest

from src.commands.registry import get_command_registry, CommandRegistry
from src.commands.base import CommandContext, CommandResultType

EXPECTED_COMMANDS = {
    "help", "plan", "sessions", "restore", "mcpstatus", "settings",
    "models", "reload", "clear", "exit", "agents", "tasks", "teams", "prompt",
}


@pytest.fixture(scope="module")
def registry():
    return get_command_registry()


class TestRegistry:
    def test_all_builtin_commands_registered(self, registry):
        names = set(registry.list_commands())
        missing = EXPECTED_COMMANDS - names
        assert not missing, f"missing commands: {missing}"

    def test_get_by_name(self, registry):
        cmd = registry.get("help")
        assert cmd is not None and cmd.name == "help"

    def test_get_by_alias(self, registry):
        assert registry.get("?") is registry.get("help")
        assert registry.get("q") is registry.get("exit")

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("nonexistent") is None

    def test_parse_input(self, registry):
        name, cmd, args = registry.parse_input("/help me")
        assert name == "help" and cmd is not None and args == "me"

    def test_parse_non_command(self, registry):
        name, cmd, args = registry.parse_input("hello world")
        assert name is None and cmd is None and args == "hello world"

    def test_parse_unknown_command(self, registry):
        name, cmd, args = registry.parse_input("/unknown x")
        assert name == "unknown" and cmd is None and args == "x"

    def test_help_text_lists_commands(self, registry):
        text = registry.get_help_text()
        assert "/help" in text
        assert "/exit" in text

    def test_register_requires_name(self):
        reg = CommandRegistry()
        from src.commands.base import Command

        class Nameless(Command):
            async def execute(self, context):
                yield None

        with pytest.raises(ValueError):
            reg.register(Nameless())


class TestOfflineCommands:
    async def test_help_command_output(self, registry):
        cmd = registry.get("help")
        results = [r async for r in cmd.execute(CommandContext())]
        assert len(results) == 1
        assert results[0].type == CommandResultType.OUTPUT
        assert "/help" in results[0].content

    async def test_exit_command_without_cli(self, registry):
        cmd = registry.get("exit")
        results = [r async for r in cmd.execute(CommandContext())]
        assert results[-1].type == CommandResultType.SUCCESS
