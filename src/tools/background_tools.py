"""Background task tools"""
import asyncio
import logging
from pydantic import BaseModel, Field
from .registry import Tool
from .background import get_background_manager

logger = logging.getLogger("Nexus")


class BackgroundRunArgs(BaseModel):
    """Background run tool arguments"""
    command: str = Field(..., description="Shell command to execute in background")
    cwd: str | None = Field(None, description="Working directory for the command")


class BackgroundRunTool(Tool):
    """Run a command in background without blocking"""

    @property
    def name(self) -> str:
        return "background_run"

    @property
    def description(self) -> str:
        return (
            "Run a shell command in background without blocking. "
            "Returns a task_id immediately so you can continue work. "
            "Use check_background to see results later. "
            "Input: command (string, required) - the command to execute in background, "
            "cwd (string, optional) - working directory"
        )

    @property
    def is_mutating(self) -> bool:
        return True

    async def execute(self, command: str, cwd: str = None, **kwargs) -> str:
        """Execute command in background"""
        bg_manager = get_background_manager()
        return await bg_manager.run(command, cwd)

    def _get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute in background"
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command"
                }
            },
            "required": ["command"]
        }


class CheckBackgroundArgs(BaseModel):
    """Check background tool arguments"""
    task_id: str | None = Field(None, description="Task ID to check (omit to list all)")


class CheckBackgroundTool(Tool):
    """Check status of background tasks"""

    @property
    def name(self) -> str:
        return "check_background"

    @property
    def description(self) -> str:
        return (
            "Check status of background tasks. "
            "Without task_id: lists all background tasks. "
            "With task_id: shows that task's status and result. "
            "Input: task_id (string, optional) - the task ID to check"
        )

    async def execute(self, task_id: str = None, **kwargs) -> str:
        """Check background task status"""
        bg_manager = get_background_manager()
        return await bg_manager.check(task_id)

    def _get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to check (omit to list all)"
                }
            },
            "required": []
        }


background_run_tool = BackgroundRunTool()
check_background_tool = CheckBackgroundTool()
