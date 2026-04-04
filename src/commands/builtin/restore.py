"""Restore command - Restore historical session"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class RestoreCommand(Command):
    """/restore - Restore historical session"""

    name = "restore"
    description = "恢复历史会话"
    aliases = ["rs"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the restore command"""
        if not context.cli:
            yield CommandResult(
                type=CommandResultType.ERROR,
                content="无法获取会话管理器"
            )
            return

        if not context.args:
            sessions = context.cli.list_sessions()
            if not sessions:
                yield CommandResult(
                    type=CommandResultType.OUTPUT,
                    content="没有已保存的会话"
                )
                return

            lines = ["请选择要恢复的会话："]
            for i, session in enumerate(sessions, 1):
                session_id = session.get("session_id", "unknown")[:8]
                title = session.get("title", "无标题")
                lines.append(f"  /restore {i}  - {title} ({session_id})")

            yield CommandResult(
                type=CommandResultType.OUTPUT,
                content="\n".join(lines)
            )
        else:
            try:
                idx = int(context.args.strip()) - 1
                success = context.cli.restore_session(idx)
                if success:
                    yield CommandResult(
                        type=CommandResultType.SUCCESS,
                        content="会话已恢复"
                    )
                else:
                    yield CommandResult(
                        type=CommandResultType.ERROR,
                        content="恢复会话失败"
                    )
            except ValueError:
                yield CommandResult(
                    type=CommandResultType.ERROR,
                    content="无效的会话编号"
                )


restore_command = RestoreCommand()
