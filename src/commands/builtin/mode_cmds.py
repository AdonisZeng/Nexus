"""Mode & configuration commands: /plan, /tasks, /models, /settings, /prompt"""
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


class PromptCommand(Command):
    """Display the current system prompt for debugging."""

    name = "prompt"
    description = "显示当前系统提示的完整内容（调试用）"
    aliases = []

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the prompt command."""
        cli = context.cli

        if hasattr(cli, 'system_prompt_builder') and cli.system_prompt_builder:
            static = cli.system_prompt_builder.build_static()
            full = cli.system_prompt_builder.build_full()

            yield CommandResult.output(
                f"=== STATIC ({len(static)} chars) ===\n{static}"
            )
            yield CommandResult.output(
                f"\n=== FULL ({len(full)} chars) ===\n{full}"
            )
        elif hasattr(cli, 'system_prompt') and cli.system_prompt:
            yield CommandResult.output(
                f"=== CURRENT ({len(cli.system_prompt)} chars) ===\n{cli.system_prompt}"
            )
        else:
            yield CommandResult.output("系统提示词未初始化")


plan_command = PlanCommand()
tasks_command = TasksCommand()
models_command = ModelsCommand()
settings_command = SettingsCommand()
prompt_command = PromptCommand()

__all__ = [
    "plan_command",
    "tasks_command",
    "models_command",
    "settings_command",
    "prompt_command",
    "ModelsCommand",
    "SettingsCommand",
    "PromptCommand",
]
