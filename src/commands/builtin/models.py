"""Models command - Display model information"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class ModelsCommand(Command):
    """/models - Show current model information"""

    name = "models"
    description = "显示当前模型信息"
    aliases = []
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the models command"""
        if context.cli and context.cli.model_adapter:
            info_text = f"当前模型: {context.cli.model_adapter.get_name()}"
        else:
            info_text = "未配置模型"

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content=info_text
        )


models_command = ModelsCommand()
