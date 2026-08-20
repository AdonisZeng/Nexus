"""Reload command - Reload skills"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class ReloadCommand(Command):
    """/reload - Reload skills"""

    name = "reload"
    description = "重新加载技能"
    aliases = []
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the reload command"""
        yield CommandResult(
            type=CommandResultType.THINKING,
            content="正在重新加载技能..."
        )

        if context.cli and hasattr(context.cli, '_reload_skills'):
            context.cli._reload_skills()

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="技能已重新加载"
        )


reload_command = ReloadCommand()