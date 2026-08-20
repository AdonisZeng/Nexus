"""Exit command - Quit application"""
import asyncio
import sys
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class ExitCommand(Command):
    """/exit - Quit application"""

    name = "exit"
    description = "退出应用"
    aliases = ["quit", "q"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the exit command"""
        yield CommandResult(
            type=CommandResultType.THINKING,
            content="正在退出..."
        )

        if context.cli:
            if context.cli.messages:
                context.cli.memory_manager.save_session(
                    context.cli.session_id,
                    context.cli.messages,
                    context.cli.current_title
                )
            asyncio.create_task(context.cli.cleanup())
            sys.exit(0)

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="再见!"
        )


exit_command = ExitCommand()