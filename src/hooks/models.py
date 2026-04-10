"""Hook models - HookEvent, HookDefinition, HookResult"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class HookEvent(Enum):
    """Unified hook events across all components."""

    # Agent lifecycle
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    ITERATION_START = "iteration_start"
    ITERATION_END = "iteration_end"
    # Tool lifecycle
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_BLOCKED = "tool_blocked"
    # Context
    CONTEXT_COMPRESSED = "context_compressed"
    # Session
    SESSION_START = "session_start"
    SESSION_END = "session_end"


@dataclass
class HookDefinition:
    """Hook definition from config or frontmatter."""

    type: Literal["subprocess", "agent"] = "subprocess"
    command: str = ""  # for subprocess type
    matcher: Optional[str] = None  # tool name filter, "*" = all, None = all
    id: Optional[str] = None  # unique identifier
    # Agent hook specific fields
    agent_prompt: str = ""  # prompt template for agent hook
    agent_max_iterations: int = 3  # max iterations for agent hook

    @classmethod
    def from_dict(cls, data: dict) -> "HookDefinition":
        """Create HookDefinition from dict."""
        return cls(
            type=data.get("type", "subprocess"),
            command=data.get("command", ""),
            matcher=data.get("matcher"),
            id=data.get("id"),
            agent_prompt=data.get("agent_prompt", ""),
            agent_max_iterations=data.get("agent_max_iterations", 3),
        )

    def to_dict(self) -> dict:
        """Convert to dict."""
        result = {
            "type": self.type,
            "command": self.command,
        }
        if self.matcher is not None:
            result["matcher"] = self.matcher
        if self.id is not None:
            result["id"] = self.id
        if self.agent_prompt:
            result["agent_prompt"] = self.agent_prompt
        if self.agent_max_iterations != 3:
            result["agent_max_iterations"] = self.agent_max_iterations
        return result


@dataclass
class HookResult:
    """Result from hook execution."""

    blocked: bool = False
    messages: list[str] = field(default_factory=list)
    updated_input: Optional[dict[str, Any]] = None
    permission_override: Optional[bool] = None  # True=allow, False=deny, None=no override

    def merge(self, other: "HookResult") -> None:
        """Merge another HookResult into this one."""
        if other.blocked:
            self.blocked = True
        self.messages.extend(other.messages)
        if other.updated_input:
            if self.updated_input is None:
                self.updated_input = other.updated_input
            else:
                self.updated_input.update(other.updated_input)
        if other.permission_override is not None:
            self.permission_override = other.permission_override


__all__ = ["HookEvent", "HookDefinition", "HookResult"]
