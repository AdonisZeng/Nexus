"""Team Manager - Manages team lifecycle and member coordination"""
import asyncio
import logging
import time
from typing import Optional

from .models import TeamConfig, TeammateConfig, TeammateStatus, MessageType
from .storage import TeamStorage
from .message_bus import MessageBus
from src.utils import get_logger

logger = get_logger("team.manager")


class TeamManager:
    """Manages team lifecycle, member creation, and coordination"""

    MAX_MEMBERS = 10

    def __init__(
        self,
        storage: Optional[TeamStorage] = None,
        message_bus: Optional[MessageBus] = None,
    ):
        self.storage = storage or TeamStorage()
        self.message_bus = message_bus or MessageBus(self.storage)
        self._member_tasks: dict[str, asyncio.Task] = {}
        self._member_configs: dict[str, TeammateConfig] = {}

    def spawn_member(
        self,
        team_name: str,
        name: str,
        role: str,
        task: str,
        tools: list[str] = None,
    ) -> str:
        """Spawn a new member in an existing team (同步版本)

        Args:
            team_name: Team name
            name: Member name
            role: Member role
            task: Task description
            tools: List of allowed tools

        Returns:
            Status string
        """
        team_config = self.storage.load_team_config(team_name)
        if not team_config:
            return f"Error: Team '{team_name}' not found"

        current_members = len(team_config.members)
        if current_members >= self.MAX_MEMBERS:
            return f"Error: Team members cannot exceed {self.MAX_MEMBERS}"

        if name in team_config.members:
            return f"Error: Member '{name}' already exists"

        member_config = TeammateConfig(
            name=name,
            role=role,
            task=task,
            tools=tools or [],
            status=TeammateStatus.INITIAL.value,
            created_at=time.time(),
            team_name=team_name,
        )

        self._member_configs[f"{team_name}:{name}"] = member_config
        self.storage.save_member_config(member_config)

        team_config.members.append(name)
        self.storage.save_team_config(team_config)

        logger.info(f"Spawned member {name} in team {team_name}")
        return f"Spawned '{name}' in team '{team_name}'"

    async def get_status(self, team_name: str) -> str:
        """Get team and all member status

        Args:
            team_name: Team name

        Returns:
            Status string
        """
        team_config = self.storage.load_team_config(team_name)
        if not team_config:
            return f"Error: Team '{team_name}' not found"

        lines = [f"Team: {team_name}", f"Status: {team_config.status}"]

        for member_name in team_config.members:
            member_config = self.storage.load_member_config(team_name, member_name)
            if member_config:
                lines.append(
                    f"  - {member_config.name} ({member_config.role}): {member_config.status}"
                )
            else:
                lines.append(f"  - {member_name}: unknown")

        return "\n".join(lines)

    async def shutdown_team(self, team_name: str) -> str:
        """Shutdown all members in the team

        Args:
            team_name: Team name

        Returns:
            Status string
        """
        team_config = self.storage.load_team_config(team_name)
        if not team_config:
            return f"Error: Team '{team_name}' not found"

        for member_name in team_config.members:
            await self.message_bus.send_shutdown_request(team_name, member_name)

        team_config.status = "done"
        self.storage.save_team_config(team_config)

        for task_key, task in list(self._member_tasks.items()):
            if task_key.startswith(f"{team_name}:"):
                task.cancel()

        logger.info(f"Shutdown team {team_name}")
        return f"Shutdown team '{team_name}'"

    def register_member_task(
        self, team_name: str, member_name: str, task: asyncio.Task
    ) -> None:
        """Register a member's asyncio task for tracking"""
        key = f"{team_name}:{member_name}"
        self._member_tasks[key] = task

    def get_member_task(
        self, team_name: str, member_name: str
    ) -> Optional[asyncio.Task]:
        """Get a member's asyncio task"""
        key = f"{team_name}:{member_name}"
        return self._member_tasks.get(key)

    def get_member_config(
        self, team_name: str, member_name: str
    ) -> Optional[TeammateConfig]:
        """Get a member's configuration"""
        key = f"{team_name}:{member_name}"
        return self._member_configs.get(key)

    def get_team_config(self, team_name: str) -> Optional[TeamConfig]:
        """Get team's configuration"""
        return self.storage.load_team_config(team_name)

    async def update_member_status(
        self, team_name: str, member_name: str, status: str
    ) -> None:
        """Update member status in storage and in-memory cache"""
        key = f"{team_name}:{member_name}"
        member_config = self.storage.load_member_config(team_name, member_name)
        if member_config:
            member_config.status = status
            member_config.last_active = time.time()
            self.storage.save_member_config(member_config)
            # 同步更新内存缓存
            self._member_configs[key] = member_config
