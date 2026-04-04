"""Agents management command for /agents"""
from typing import AsyncIterator

from src.commands.base import Command, CommandContext, CommandResult, CommandResultType
from src.cli.rich_ui import console, input_with_prompt
from .agents_utils import AgentConfigEditor
from .agents_create import run_create_flow
from .agents_edit import run_edit_flow


class AgentsCommand(Command):
    """Manage subagents"""

    name = "agents"
    description = "管理子代理 (list, create, edit, delete)"
    aliases = ["agent"]

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute /agents command"""
        agents = AgentConfigEditor.load_all_agents()

        # Main menu
        console.print("\n[bold]SubAgent 管理[/bold]\n")
        console.print("[1] Create SubAgent")
        console.print(f"[2+] 已有 Agent ({len(agents)}个)")

        # List existing agents
        for i, agent in enumerate(agents, 3):
            desc_preview = agent.description[:40] if len(agent.description) > 40 else agent.description
            console.print(f"[{i}] {agent.name} - {desc_preview}")

        console.print("[0] 退出")

        choice = input_with_prompt("> ").strip()

        if choice == "0":
            yield CommandResult(type=CommandResultType.OUTPUT, content="已退出")
            return

        if choice == "1":
            # Create new agent
            async for result in run_create_flow(context):
                yield result
            return

        # Select existing agent
        try:
            idx = int(choice) - 3
            if 0 <= idx < len(agents):
                config = agents[idx]
                async for result in run_edit_flow(config):
                    yield result
            else:
                yield CommandResult(type=CommandResultType.ERROR, content="无效选择")
        except ValueError:
            yield CommandResult(type=CommandResultType.ERROR, content="请输入有效编号")


# Singleton instance
agents_command = AgentsCommand()


__all__ = ["agents_command"]
