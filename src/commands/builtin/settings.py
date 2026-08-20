"""Settings command - Edit config.yaml"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class SettingsCommand(Command):
    """/settings - Edit config.yaml"""

    name = "settings"
    description = "编辑配置文件"
    aliases = []
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the settings command"""
        if context.cli:
            if hasattr(context.cli, '_handle_settings'):
                context.cli._handle_settings()
                yield CommandResult(
                    type=CommandResultType.SUCCESS,
                    content="设置已完成"
                )
            else:
                yield CommandResult(
                    type=CommandResultType.OUTPUT,
                    content="可通过编辑 config.yaml 来修改设置"
                )
        else:
            yield CommandResult(
                type=CommandResultType.OUTPUT,
                content="可通过编辑 config.yaml 来修改设置"
            )


settings_command = SettingsCommand()
