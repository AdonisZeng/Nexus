"""Team Monitor - Monitoring and fault tolerance for team members"""
import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable, List

from .models import (
    MemberMonitorState,
    MonitorState,
    StatusReport,
    MessageType,
)
from .message_bus import MessageBus
from .manager import TeamManager
from src.utils import get_logger

logger = get_logger("team.monitor")


class TeamMonitor:
    """Monitors team members for timeouts and initiates degradation

    Implements three-layer timeout detection:
    - Activity timeout (5 min): No status update
    - Response timeout (2 min): No response after warning
    - Task timeout (3 warnings): Initiate degradation
    """

    ACTIVITY_TIMEOUT = 300
    RESPONSE_TIMEOUT = 120
    MAX_WARNINGS = 3

    def __init__(
        self,
        manager: TeamManager,
        message_bus: MessageBus,
        on_degrade: Optional[Callable[[str, StatusReport], Awaitable[None]]] = None,
        on_status_update: Optional[Callable[[str, StatusReport], Awaitable[None]]] = None,
    ):
        self.manager = manager
        self.message_bus = message_bus
        self.on_degrade = on_degrade
        self.on_status_update = on_status_update

        self._member_states: dict[str, MemberMonitorState] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self, team_name: str) -> None:
        """Start monitoring for a team"""
        self._running = True
        team_config = self.manager.get_team_config(team_name)
        if team_config:
            for member_name in team_config.members:
                key = f"{team_name}:{member_name}"
                self._member_states[key] = MemberMonitorState(member_name=member_name)

        logger.info(f"TeamMonitor started for {team_name}")
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Stop monitoring"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._member_states.clear()
        logger.info("TeamMonitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop - polls every 30 seconds"""
        while self._running:
            try:
                await self._check_all_members()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(30)

    async def _check_all_members(self) -> None:
        """Check all member states for timeouts"""
        for key, state in list(self._member_states.items()):
            try:
                await self._check_member(key, state)
            except Exception as e:
                logger.error(f"Error checking member {state.member_name}: {e}")

    async def _check_member(self, key: str, state: MemberMonitorState) -> None:
        """Check a single member for timeout conditions"""
        if state.state == MonitorState.DEGRADED.value:
            return

        if state.is_activity_timeout(self.ACTIVITY_TIMEOUT):
            if state.state == MonitorState.NORMAL.value:
                await self._send_warning(key, state, 1)
            elif state.warning_count > 0:
                if state.is_response_timeout(self.RESPONSE_TIMEOUT):
                    await self._escalate_warning(key, state)

    async def _send_warning(self, key: str, state: MemberMonitorState, level: int) -> None:
        """Send a warning to a member"""
        team_name, member_name = key.split(":", 1)
        state.warning_count = level

        warnings = {
            1: f"注意到你已经有一段时间没有更新状态了。请问当前任务进展如何？是否遇到了什么困难？",
            2: f"请注意，你的任务已经延迟。请尽快完成任务的关键部分并报告进度。如有阻塞问题，请明确说明。",
            3: f"这是最后一次提醒。请立即完成任务或报告明确的阻塞原因。如仍无响应，Lead 将接管你的任务。",
        }

        content = warnings.get(level, f"Warning level {level}")
        await self.message_bus.send_warning(
            team_name, "lead", member_name, content, level
        )

        state.state = f"warn_{level}"
        logger.warning(f"Warning {level} sent to {member_name}")

    async def _escalate_warning(self, key: str, state: MemberMonitorState) -> None:
        """Escalate warning to next level"""
        next_level = state.warning_count + 1

        if next_level >= self.MAX_WARNINGS:
            state.state = MonitorState.DEGRADED.value
            await self._initiate_degradation(key, state)
        else:
            await self._send_warning(key, state, next_level)

    async def _initiate_degradation(
        self, key: str, state: MemberMonitorState
    ) -> None:
        """Initiate degradation for a stuck member"""
        team_name, member_name = key.split(":", 1)

        logger.warning(f"Initiating degradation for {member_name}")

        await self.message_bus.send_result(
            team_name, member_name, "lead",
            f"Degradation initiated for {member_name}. Lead will take over task."
        )

        progress_report = StatusReport(
            progress=state.progress,
            current_action=state.current_action,
            completed=state.completed_items,
            remaining=state.remaining_items,
            blockers=["Member timeout after 3 warnings"],
        )

        if self.on_degrade:
            await self.on_degrade(member_name, progress_report)

    def update_member_status(
        self, team_name: str, member_name: str, report: StatusReport
    ) -> None:
        """Update member status from a status report"""
        key = f"{team_name}:{member_name}"
        if key not in self._member_states:
            self._member_states[key] = MemberMonitorState(member_name=member_name)

        state = self._member_states[key]
        state.update_from_report(report)

        if self.on_status_update:
            asyncio.create_task(
                self.on_status_update(member_name, report)
            )

    def get_all_states(self) -> dict[str, MemberMonitorState]:
        """Get all member monitor states"""
        return self._member_states.copy()

    def get_member_state(self, team_name: str, member_name: str) -> Optional[MemberMonitorState]:
        """Get monitor state for a specific member"""
        key = f"{team_name}:{member_name}"
        return self._member_states.get(key)

    def generate_takeover_tasks(
        self, member_name: str, report: StatusReport
    ) -> list[dict]:
        """Generate tasks for takeover from a degraded member

        Args:
            member_name: Name of the degraded member
            report: Status report from the member

        Returns:
            List of task dicts to continue the work
        """
        tasks = []

        for item in report.remaining:
            tasks.append({
                "id": len(tasks) + 1,
                "subject": f"[继续 {member_name}] {item}",
                "description": f"继续完成 {member_name} 未完成的任务: {item}",
                "blocked_by": [],
            })

        return tasks
