"""Request Tracker - Async request tracking with request_id correlation"""
import asyncio
import logging
import uuid
from typing import Optional

from .models import ShutdownRequest, PlanRequest
from src.utils import get_logger

logger = get_logger("team.tracker")


class RequestTracker:
    """Async request tracker for shutdown and plan approval protocols

    Uses request_id for correlating async request/response pairs.
    Thread-safe with asyncio.Lock.
    """

    def __init__(self):
        self._shutdown_requests: dict[str, ShutdownRequest] = {}
        self._plan_requests: dict[str, PlanRequest] = {}
        self._lock = asyncio.Lock()

    def _generate_request_id(self) -> str:
        """Generate a short unique request ID (UUID first 8 chars)"""
        return uuid.uuid4().hex[:8]

    async def create_shutdown_request(
        self, team_name: str, target: str
    ) -> str:
        """Create a new shutdown request and return request_id

        @param team_name: Team name
        @param target: Target teammate name
        @return: Generated request_id
        """
        request_id = self._generate_request_id()
        request = ShutdownRequest(
            request_id=request_id,
            team_name=team_name,
            target=target,
            status="pending",
        )
        async with self._lock:
            self._shutdown_requests[request_id] = request
        logger.info(f"Created shutdown request {request_id} for {target}")
        return request_id

    async def create_plan_request(
        self, team_name: str, from_: str, plan: str
    ) -> str:
        """Create a new plan approval request and return request_id

        @param team_name: Team name
        @param from_: Teammate name submitting the plan
        @param plan: Plan text
        @return: Generated request_id
        """
        request_id = self._generate_request_id()
        request = PlanRequest(
            request_id=request_id,
            team_name=team_name,
            from_=from_,
            plan=plan,
            status="pending",
        )
        async with self._lock:
            self._plan_requests[request_id] = request
        logger.info(f"Created plan request {request_id} from {from_}")
        return request_id

    async def update_shutdown_status(
        self, request_id: str, status: str
    ) -> bool:
        """Update shutdown request status

        @param request_id: Request ID to update
        @param status: New status (approved/rejected)
        @return: True if updated, False if not found
        """
        async with self._lock:
            if request_id in self._shutdown_requests:
                self._shutdown_requests[request_id].status = status
                logger.info(f"Updated shutdown request {request_id} to {status}")
                return True
        return False

    async def update_plan_status(
        self, request_id: str, status: str, feedback: str = ""
    ) -> bool:
        """Update plan request status

        @param request_id: Request ID to update
        @param status: New status (approved/rejected)
        @param feedback: Optional feedback message
        @return: True if updated, False if not found
        """
        async with self._lock:
            if request_id in self._plan_requests:
                self._plan_requests[request_id].status = status
                self._plan_requests[request_id].feedback = feedback
                logger.info(f"Updated plan request {request_id} to {status}")
                return True
        return False

    async def get_shutdown_request(
        self, request_id: str
    ) -> Optional[ShutdownRequest]:
        """Get shutdown request by request_id

        @param request_id: Request ID to look up
        @return: ShutdownRequest or None if not found
        """
        async with self._lock:
            return self._shutdown_requests.get(request_id)

    async def get_plan_request(self, request_id: str) -> Optional[PlanRequest]:
        """Get plan request by request_id

        @param request_id: Request ID to look up
        @return: PlanRequest or None if not found
        """
        async with self._lock:
            return self._plan_requests.get(request_id)

    async def get_pending_requests(
        self, team_name: str
    ) -> dict[str, list[dict]]:
        """Get all pending requests for a team

        @param team_name: Team name
        @return: Dict with 'shutdown' and 'plan' lists
        """
        pending = {"shutdown": [], "plan": []}
        async with self._lock:
            for req in self._shutdown_requests.values():
                if req.team_name == team_name and req.status == "pending":
                    pending["shutdown"].append(req.to_dict())
            for req in self._plan_requests.values():
                if req.team_name == team_name and req.status == "pending":
                    pending["plan"].append(req.to_dict())
        return pending

    async def clear_team_requests(self, team_name: str) -> None:
        """Clear all requests for a team (on team shutdown)

        @param team_name: Team name to clear requests for
        """
        async with self._lock:
            self._shutdown_requests = {
                k: v
                for k, v in self._shutdown_requests.items()
                if v.team_name != team_name
            }
            self._plan_requests = {
                k: v
                for k, v in self._plan_requests.items()
                if v.team_name != team_name
            }
        logger.info(f"Cleared requests for team {team_name}")

    def get_all_requests(self) -> dict[str, dict]:
        """Get all requests (for debugging)

        @return: Dict with all shutdown and plan requests
        """
        return {
            "shutdown": {k: v.to_dict() for k, v in self._shutdown_requests.items()},
            "plan": {k: v.to_dict() for k, v in self._plan_requests.items()},
        }
