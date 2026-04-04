"""Tasks command - Enable tasks mode for complex projects with dependency graph"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class TasksCommand(Command):
    """/tasks - Enable tasks mode for complex projects with dependency graph"""

    name = "tasks"
    description = "启用 Tasks 模式处理复杂项目任务（支持依赖图和持久化）"
    aliases = ["t"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the tasks command - activates tasks mode"""
        if context.cli:
            context.cli.enter_tasks_mode()
            yield CommandResult(
                type=CommandResultType.SUCCESS,
                content="Tasks 模式已启用，请输入任务描述"
            )
        else:
            yield CommandResult(
                type=CommandResultType.ERROR,
                content="Tasks 命令无法执行"
            )


tasks_command = TasksCommand()
