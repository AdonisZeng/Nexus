"""Clear command - Clear chat"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class ClearCommand(Command):
    """/clear - Clear chat"""

    name = "clear"
    description = "清空对话"
    aliases = ["cls"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the clear command"""
        if context.cli:
            if context.cli.messages:
                context.cli.messages = []
                context.cli.session_id = str(hash(context.cli.session_id) % 10**8)
                context.cli.current_title = "新对话"
                yield CommandResult(
                    type=CommandResultType.SUCCESS,
                    content="对话已清空，已开始新对话"
                )
                return

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="对话已清空"
        )


clear_command = ClearCommand()