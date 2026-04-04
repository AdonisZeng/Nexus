"""Plan command - Enable planning mode for complex tasks"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class PlanCommand(Command):
    """/plan - Enable planning mode for complex tasks"""

    name = "plan"
    description = "启用计划模式处理复杂任务"
    aliases = ["p"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the plan command - activates plan mode"""
        if context.cli:
            context.cli.enter_plan_mode()
            yield CommandResult(
                type=CommandResultType.SUCCESS,
                content="计划模式已启用，请输入任务描述"
            )
        else:
            yield CommandResult(
                type=CommandResultType.ERROR,
                content="计划命令无法执行"
            )


plan_command = PlanCommand()
