"""Agent types and events"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Optional

from .loop import AgentLoop, LoopEvent, LoopCallbacks, LoopMetrics
from .work_item import WorkItem, WorkItemSource


class EventType(Enum):
    """Event types for the agent event stream"""
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"
    ERROR = "error"
    WARNING = "warning"
    DONE = "done"


@dataclass
class AgentEvent:
    """Event emitted by the agent engine"""
    type: EventType
    content: str
    metadata: dict = field(default_factory=dict)

    def __str__(self):
        return f"[{self.type.value}] {self.content}"


@dataclass
class ToolDefinition:
    """Tool definition for the model"""
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolResult:
    """Result from tool execution"""
    success: bool
    result: Any = None
    error: str = None


class AgentEngine:
    """Core agent engine that produces event streams"""

    def __init__(self, model_adapter, tool_registry):
        self.model = model_adapter
        self.tools = tool_registry

    async def execute_stream(self, task: str) -> AsyncIterator[AgentEvent]:
        """Execute task and yield events"""
        raise NotImplementedError

    async def chat(self, messages: list[dict]) -> str:
        """Simple chat without tools"""
        return await self.model.chat(messages)