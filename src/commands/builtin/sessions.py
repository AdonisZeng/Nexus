"""Sessions command - Display saved session list"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class SessionsCommand(Command):
    """/sessions - Display saved session list"""

    name = "sessions"
    description = "显示已保存的会话列表"
    aliases = ["session", "ls"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the sessions command"""
        if not context.cli:
            yield CommandResult(
                type=CommandResultType.ERROR,
                content="无法获取会话管理器"
            )
            return

        sessions = context.cli.list_sessions()
        if not sessions:
            yield CommandResult(
                type=CommandResultType.OUTPUT,
                content="没有已保存的会话"
            )
            return

        lines = ["已保存的会话："]
        for i, session in enumerate(sessions, 1):
            session_id = session.get("session_id", "unknown")[:8]
            title = session.get("title", "无标题")
            updated = session.get("updated", "unknown")
            lines.append(f"  {i}. {title} ({session_id}) - {updated}")

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="\n".join(lines)
        )


sessions_command = SessionsCommand()
