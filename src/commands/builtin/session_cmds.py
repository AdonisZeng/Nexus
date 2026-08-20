"""Session-related commands: /sessions, /restore, /clear, /exit, /reload"""
import asyncio
import sys
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


class ClearCommand(Command):
    """/clear - Clear chat"""

    name = "clear"
    description = "清空对话"
    aliases = ["cls"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the clear command"""
        if context.cli:
            if context.cli.messages:
                context.cli.messages = []
                context.cli.session_id = str(hash(context.cli.session_id) % 10**8)
                context.cli.current_title = "新对话"
                yield CommandResult(
                    type=CommandResultType.SUCCESS,
                    content="对话已清空，已开始新对话"
                )
                return

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="对话已清空"
        )


class ExitCommand(Command):
    """/exit - Quit application"""

    name = "exit"
    description = "退出应用"
    aliases = ["quit", "q"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the exit command"""
        yield CommandResult(
            type=CommandResultType.THINKING,
            content="正在退出..."
        )

        if context.cli:
            if context.cli.messages:
                context.cli.memory_manager.save_session(
                    context.cli.session_id,
                    context.cli.messages,
                    context.cli.current_title
                )
            asyncio.create_task(context.cli.cleanup())
            sys.exit(0)

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="再见!"
        )


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


sessions_command = SessionsCommand()
restore_command = RestoreCommand()
clear_command = ClearCommand()
exit_command = ExitCommand()
reload_command = ReloadCommand()

__all__ = [
    "sessions_command",
    "restore_command",
    "clear_command",
    "exit_command",
    "reload_command",
    "ClearCommand",
    "ExitCommand",
    "ReloadCommand",
]
