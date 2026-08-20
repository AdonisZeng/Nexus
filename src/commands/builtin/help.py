"""Help command - Show available commands"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType
from ..registry import get_command_registry


class HelpCommand(Command):
    """/help - Show available commands"""

    name = "help"
    description = "显示可用命令"
    aliases = ["?"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the help command"""
        registry = get_command_registry()
        help_text = registry.get_help_text()
        yield CommandResult(
            type=CommandResultType.OUTPUT,
            content=help_text
        )


help_command = HelpCommand()
