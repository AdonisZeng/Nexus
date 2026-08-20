"""Prompt command - Display assembled system prompt for debugging."""
from typing import AsyncIterator

from src.commands.base import Command, CommandContext, CommandResult


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


# Global instance for registry
prompt_command = PromptCommand()
