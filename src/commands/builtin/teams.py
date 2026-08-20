"""Teams command - Manage Agent Teams"""
from typing import AsyncIterator
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm

from ..base import Command, CommandContext, CommandResult, CommandResultType
from src.team.storage import TeamStorage

console = Console()


class TeamsCommand(Command):
    """/teams - 管理 Agent Team"""

    name = "teams"
    description = "管理 Agent Team - 列出、查看、删除团队"
    aliases = ["team"]
    requires_context = False

    def __init__(self):
        self.storage = TeamStorage()

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the teams command - shows team management interface"""
        while True:
            teams = self.storage.list_teams()

            if not teams:
                yield CommandResult(
                    type=CommandResultType.SUCCESS,
                    content="当前没有 Agent Team"
                )
                return

            self._display_teams_list(teams)

            choice = console.input("\n[cyan]请输入选择: [/cyan]").strip()

            if choice == "q":
                return

            if choice.startswith("d"):
                team_idx_str = choice[1:].strip()
                if team_idx_str.isdigit():
                    team_idx = int(team_idx_str) - 1
                    if 0 <= team_idx < len(teams):
                        team_name = teams[team_idx]
                        if await self._confirm_delete(team_name):
                            self.storage.delete_team(team_name)
                            console.print(f"[green]✓ 已删除团队: {team_name}[/green]")
                    continue
                else:
                    console.print("[red]无效的删除格式，请使用 d[编号] 格式[/red]")
                    continue

            if choice.isdigit():
                team_idx = int(choice) - 1
                if 0 <= team_idx < len(teams):
                    team_name = teams[team_idx]
                    self._display_team_members(team_name)
                    continue
                else:
                    console.print("[red]无效的编号[/red]")
                    continue

            console.print("[yellow]无效的选择[/yellow]")

    def _display_teams_list(self, teams: list[str]) -> None:
        """Display teams list in a formatted table"""
        console.print("\n")
        console.print(Panel.fit(
            "[bold cyan]Agent Team 管理界面[/bold cyan]",
            border_style="cyan"
        ))

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column("团队名称", min_width=20)
        table.add_column("成员数", justify="center", width=8)
        table.add_column("创建时间", min_width=18)
        table.add_column("状态", width=10)

        for idx, team_name in enumerate(teams, 1):
            team_config = self.storage.load_team_config(team_name)
            if team_config:
                created_time = datetime.fromtimestamp(team_config.created_at).strftime("%Y-%m-%d %H:%M")
                member_count = len(team_config.members)
                status = team_config.status
                table.add_row(
                    str(idx),
                    team_name,
                    str(member_count),
                    created_time,
                    status
                )

        console.print(table)
        console.print("\n[dim]操作: [编号]查看成员 | d[编号]删除 | q退出[/dim]")

    def _display_team_members(self, team_name: str) -> None:
        """Display team member details"""
        team_config = self.storage.load_team_config(team_name)
        if not team_config:
            console.print(f"[red]无法加载团队配置: {team_name}[/red]")
            return

        members = self.storage.list_members(team_name)

        console.print("\n")
        console.print(Panel.fit(
            f"[bold cyan]团队详情: {team_name}[/bold cyan]",
            border_style="cyan"
        ))

        console.print(f"[bold]状态:[/bold] {team_config.status}")
        console.print(f"[bold]成员数:[/bold] {len(members)}\n")

        if members:
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("#", style="dim", width=4)
            table.add_column("成员名称", min_width=18)
            table.add_column("角色", min_width=12)
            table.add_column("状态", width=10)
            table.add_column("最近活动", min_width=18)

            for idx, member_name in enumerate(members, 1):
                member_config = self.storage.load_member_config(team_name, member_name)
                if member_config:
                    last_active = datetime.fromtimestamp(member_config.last_active).strftime("%Y-%m-%d %H:%M")
                    table.add_row(
                        str(idx),
                        member_config.name,
                        member_config.role,
                        member_config.status,
                        last_active
                    )

            console.print(table)

        console.print("\n[dim]操作: [b]返回列表 | d删除此团队[/dim]")

        choice = console.input("\n[cyan]请输入选择: [/cyan]").strip().lower()

        if choice == "d":
            self._confirm_and_delete(team_name)
        elif choice == "q":
            return

    def _confirm_and_delete(self, team_name: str) -> None:
        """Confirm and delete a team"""
        console.print(f"\n[bold red]⚠️ 确认删除团队 \"{team_name}\"？[/bold red]")
        console.print("[dim]此操作将删除团队及其所有成员数据，无法撤销。[/dim]")

        if Confirm.ask(
            "请输入 [y]确认删除 [n]取消",
            choices=["y", "n"],
            default="n"
        ):
            self.storage.delete_team(team_name)
            console.print(f"[green]✓ 已删除团队: {team_name}[/green]")

    async def _confirm_delete(self, team_name: str) -> bool:
        """Confirm deletion with user"""
        console.print(f"\n[bold red]⚠️ 确认删除团队 \"{team_name}\"？[/bold red]")
        console.print("[dim]此操作将删除团队及其所有成员数据，无法撤销。[/dim]")

        return Confirm.ask(
            "请输入 [y]确认删除 [n]取消",
            choices=["y", "n"],
            default="n"
        )


teams_command = TeamsCommand()
