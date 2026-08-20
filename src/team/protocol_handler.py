"""Protocol Handler - Unified handler for Shutdown and Plan Approval protocols"""
import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from .tracker import RequestTracker
from .message_bus import MessageBus
from .models import MessageType
from src.utils import get_logger

if TYPE_CHECKING:
    from .teammate import Teammate

logger = get_logger("team.protocol_handler")


class ProtocolHandler:
    """Handles Shutdown and Plan Approval protocols

    Provides unified interface for protocol operations.
    Uses RequestTracker for request_id correlation.
    """

    def __init__(self, tracker: RequestTracker, message_bus: MessageBus):
        self.tracker = tracker
        self.message_bus = message_bus

    async def handle_shutdown_request(
        self, team_name: str, target: str, from_: str = "lead"
    ) -> str:
        """Send shutdown request to a teammate

        @param team_name: Team name
        @param target: Target teammate name
        @param from_: Sender name (default: lead)
        @return: Status message with request_id
        """
        request_id = await self.tracker.create_shutdown_request(team_name, target)

        await self.message_bus.send_shutdown_request(team_name, target, from_)

        logger.info(f"Sent shutdown request {request_id} to {target}")
        return f"Shutdown request {request_id} sent to '{target}' (status: pending)"

    async def handle_shutdown_response(
        self,
        team_name: str,
        from_: str,
        to: str,
        request_id: str,
        approve: bool,
        reason: str = "",
    ) -> str:
        """Handle shutdown response from teammate

        @param team_name: Team name
        @param from_: Teammate name
        @param to: Receiver name (usually lead)
        @param request_id: Request ID from the original shutdown_request
        @param approve: Whether shutdown is approved
        @param reason: Optional reason for approve/reject
        @return: Status message
        """
        await self.tracker.update_shutdown_status(
            request_id, "approved" if approve else "rejected"
        )

        await self.message_bus.send(
            team_name,
            from_,
            to,
            reason or ("Approved shutdown" if approve else "Rejected shutdown"),
            MessageType.SHUTDOWN_RESPONSE.value,
            metadata={"request_id": request_id, "approve": approve},
        )

        status = "approved" if approve else "rejected"
        logger.info(f"Shutdown {status} by {from_} (request_id={request_id})")
        return f"Shutdown {status} by '{from_}'"

    async def handle_plan_submission(
        self, team_name: str, from_: str, to: str, plan: str
    ) -> str:
        """Handle plan approval submission from teammate

        @param team_name: Team name
        @param from_: Teammate name submitting the plan
        @param to: Receiver name (usually lead)
        @param plan: Plan text
        @return: Status message with request_id
        """
        request_id = await self.tracker.create_plan_request(team_name, from_, plan)

        await self.message_bus.send_plan_request(team_name, from_, to, plan, request_id)

        logger.info(f"Plan submitted by {from_} (request_id={request_id})")
        return f"Plan submitted (request_id={request_id}). Waiting for lead approval."

    async def handle_plan_review(
        self,
        team_name: str,
        from_: str,
        to: str,
        request_id: str,
        approve: bool,
        feedback: str = "",
    ) -> str:
        """Handle plan review from lead

        @param team_name: Team name
        @param from_: Sender name (lead)
        @param to: Teammate name to receive the response
        @param request_id: Request ID from the original plan submission
        @param approve: Whether plan is approved
        @param feedback: Optional feedback message
        @return: Status message
        """
        await self.tracker.update_plan_status(
            request_id, "approved" if approve else "rejected", feedback
        )

        await self.message_bus.send_plan_response(
            team_name, from_, to, request_id, approve, feedback
        )

        status = "approved" if approve else "rejected"
        logger.info(f"Plan {status} for {to} (request_id={request_id})")
        return f"Plan {status} for '{to}'"

    async def get_shutdown_status(self, request_id: str) -> dict:
        """Get status of a shutdown request

        @param request_id: Request ID to look up
        @return: Dict with request status
        """
        request = await self.tracker.get_shutdown_request(request_id)
        if request:
            return {
                "request_id": request.request_id,
                "target": request.target,
                "status": request.status,
                "created_at": request.created_at,
            }
        return {"error": f"Shutdown request '{request_id}' not found"}

    async def get_plan_status(self, request_id: str) -> dict:
        """Get status of a plan approval request

        @param request_id: Request ID to look up
        @return: Dict with request status
        """
        request = await self.tracker.get_plan_request(request_id)
        if request:
            return {
                "request_id": request.request_id,
                "from": request.from_,
                "plan": request.plan[:200] + "..." if len(request.plan) > 200 else request.plan,
                "status": request.status,
                "feedback": request.feedback,
                "created_at": request.created_at,
            }
        return {"error": f"Plan request '{request_id}' not found"}

    async def get_pending_requests(self, team_name: str) -> dict:
        """Get all pending requests for a team

        @param team_name: Team name
        @return: Dict with pending shutdown and plan requests
        """
        return await self.tracker.get_pending_requests(team_name)

    async def notify_teammate_shutdown(
        self, teammate: "Teammate", request_id: str
    ) -> None:
        """Notify a teammate to shut down (called after approved)

        @param teammate: Teammate instance to shut down
        @param request_id: Request ID for logging
        """
        logger.info(f"Notifying {teammate.name} to shutdown (request_id={request_id})")
        await teammate.stop()

    async def clear_team_protocols(self, team_name: str) -> None:
        """Clear all protocol state for a team

        @param team_name: Team name
        """
        await self.tracker.clear_team_requests(team_name)
        logger.info(f"Cleared all protocol state for team {team_name}")
