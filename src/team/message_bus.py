"""Message Bus - Async message queue with persistence"""
import asyncio
import logging
import time
from typing import Optional

from .models import Message, MessageType
from .storage import TeamStorage
from src.utils import get_logger

logger = get_logger("team.message_bus")


class MessageBus:
    """Async message bus with JSONL persistence

    Each member has their own inbox queue for receiving messages.
    Messages are immediately persisted to JSONL files for crash recovery.
    """

    def __init__(self, storage: Optional[TeamStorage] = None):
        self.storage = storage or TeamStorage()
        self._queues: dict[str, asyncio.Queue] = {}
        self._registered_members: set[str] = {}

    async def register_member(self, team_name: str, member_name: str) -> None:
        """Register a member to receive messages"""
        key = self._make_key(team_name, member_name)
        if key not in self._queues:
            self._queues[key] = asyncio.Queue()
        self._registered_members[key] = team_name
        logger.debug(f"Registered member: {member_name} in {team_name}")

    async def unregister_member(self, team_name: str, member_name: str) -> None:
        """Unregister a member"""
        key = self._make_key(team_name, member_name)
        self._queues.pop(key, None)
        self._registered_members.pop(key, None)
        logger.debug(f"Unregistered member: {member_name} in {team_name}")

    async def send(
        self,
        team_name: str,
        from_: str,
        to: str,
        content: str,
        msg_type: str = MessageType.MESSAGE.value,
        metadata: dict = None,
    ) -> str:
        """Send a message to a member's inbox

        Args:
            team_name: Team name
            from_: Sender name
            to: Receiver name
            content: Message content
            msg_type: Message type
            metadata: Additional metadata

        Returns:
            Status string
        """
        key = self._make_key(team_name, to)
        message = Message(
            type=msg_type,
            from_=from_,
            to=to,
            content=content,
            timestamp=time.time(),
            metadata=metadata or {},
        )

        self.storage.append_to_inbox(team_name, to, message)

        if key in self._queues:
            await self._queues[key].put(message)

        logger.info(f"Message sent: {from_} -> {to} ({msg_type}) in {team_name}")
        return f"Sent {msg_type} to {to}"

    async def receive(self, team_name: str, member_name: str) -> list[Message]:
        """Receive all pending messages for a member

        Returns messages from both persistent storage and in-memory queue.
        Persistent storage is cleared after reading.

        Args:
            team_name: Team name
            member_name: Member name

        Returns:
            List of messages
        """
        key = self._make_key(team_name, member_name)
        messages = []

        try:
            msg = await asyncio.wait_for(
                self._queues[key].get(), timeout=0.05
            )
            if msg:
                messages.append(msg)
        except asyncio.TimeoutError:
            pass

        stored_messages = self.storage.read_and_clear_inbox(team_name, member_name)
        messages.extend(stored_messages)

        if messages:
            logger.debug(f"Received {len(messages)} messages for {member_name}")

        return messages

    async def broadcast(
        self,
        team_name: str,
        from_: str,
        content: str,
        members: list[str],
        msg_type: str = MessageType.BROADCAST.value,
    ) -> str:
        """Broadcast a message to all members

        Args:
            team_name: Team name
            from_: Sender name
            content: Message content
            members: List of member names to broadcast to
            msg_type: Message type

        Returns:
            Status string
        """
        count = 0
        for member_name in members:
            if member_name != from_:
                await self.send(team_name, from_, member_name, content, msg_type)
                count += 1

        logger.info(f"Broadcast from {from_} to {count} members in {team_name}")
        return f"Broadcast to {count} members"

    async def send_task(
        self, team_name: str, to: str, task: str, from_: str = "lead"
    ) -> str:
        """Convenience method to send a task message"""
        return await self.send(
            team_name, from_, to, task, MessageType.TASK.value
        )

    async def send_status(
        self, team_name: str, from_: str, to: str, content: str
    ) -> str:
        """Convenience method to send a status report"""
        return await self.send(
            team_name, from_, to, content, MessageType.STATUS.value
        )

    async def send_result(
        self, team_name: str, from_: str, to: str, content: str
    ) -> str:
        """Convenience method to send a result"""
        return await self.send(
            team_name, from_, to, content, MessageType.RESULT.value
        )

    async def send_warning(
        self, team_name: str, from_: str, to: str, content: str, level: int
    ) -> str:
        """Convenience method to send a warning"""
        return await self.send(
            team_name,
            from_,
            to,
            content,
            MessageType.WARNING.value,
            metadata={"level": level},
        )

    async def send_shutdown_request(
        self, team_name: str, to: str, from_: str = "lead"
    ) -> str:
        """Convenience method to send shutdown request"""
        return await self.send(
            team_name, from_, to, "Please shutdown", MessageType.SHUTDOWN_REQUEST.value
        )

    async def send_plan_request(
        self,
        team_name: str,
        from_: str,
        to: str,
        plan: str,
        request_id: str,
    ) -> str:
        """Convenience method to send plan approval request to lead

        @param team_name: Team name
        @param from_: Teammate name submitting the plan
        @param to: Receiver name (usually lead)
        @param plan: Plan text
        @param request_id: Request ID for correlation
        @return: Status string
        """
        return await self.send(
            team_name,
            from_,
            to,
            plan,
            MessageType.PLAN_APPROVAL.value,
            metadata={"request_id": request_id, "plan": plan},
        )

    async def send_plan_response(
        self,
        team_name: str,
        from_: str,
        to: str,
        request_id: str,
        approve: bool,
        feedback: str = "",
    ) -> str:
        """Convenience method to send plan approval response to teammate

        @param team_name: Team name
        @param from_: Sender name (usually lead)
        @param to: Teammate name to respond to
        @param request_id: Request ID for correlation
        @param approve: Whether plan is approved
        @param feedback: Optional feedback message
        @return: Status string
        """
        content = (
            f"Plan {'approved' if approve else 'rejected'}"
            + (f": {feedback}" if feedback else "")
        )
        return await self.send(
            team_name,
            from_,
            to,
            content,
            MessageType.PLAN_APPROVAL_RESPONSE.value,
            metadata={"request_id": request_id, "approve": approve, "feedback": feedback},
        )

    def _make_key(self, team_name: str, member_name: str) -> str:
        """Create a unique key for queue lookup"""
        return f"{team_name}:{member_name}"

    def get_registered_members(self, team_name: str) -> list[str]:
        """Get list of registered members for a team"""
        prefix = f"{team_name}:"
        return [
            key.replace(prefix, "")
            for key in self._registered_members.keys()
            if key.startswith(prefix)
        ]
