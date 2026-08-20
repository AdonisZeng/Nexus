"""
Agent Context - Unified context management for Nexus Agent System

This module provides the unified context data structure for all three agents:
- agent-loop: Uses for iteration tracking and termination conditions
- agent-context: Uses for message and memory management
- agent-skills: Uses for skill execution context
"""

from dataclasses import dataclass, field
from typing import Optional, Any
import time
import uuid


@dataclass
class ContextMessage:
    """Single message in the conversation.

    Attributes:
        role: Message role (user/assistant/system/tool)
        content: Message content
        timestamp: Unix timestamp when created
        metadata: Additional metadata (tool_name, iteration, etc.)
        token_count: Estimated token count for this message
    """
    role: str  # user/assistant/system/tool
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)
    token_count: int = 0

    def to_dict(self) -> dict:
        """Convert to simple dict format for API compatibility."""
        return {
            "role": self.role,
            "content": self.content
        }

    def to_full_dict(self) -> dict:
        """Convert to full dict with all metadata."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "token_count": self.token_count
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextMessage":
        """Create from dict (supports both simple and full format)."""
        if "timestamp" in data:
            return cls(
                role=data["role"],
                content=data["content"],
                timestamp=data.get("timestamp", time.time()),
                metadata=data.get("metadata", {}),
                token_count=data.get("token_count", 0)
            )
        return cls(role=data["role"], content=data["content"])


@dataclass
class ToolCallEntry:
    """Record of a single tool call."""
    tool_name: str
    arguments: dict
    start_time: float
    end_time: Optional[float] = None
    success: bool = False
    result: Optional[str] = None
    error: Optional[str] = None
    iteration: int = 0


@dataclass
class ConversationState:
    """State tracking for the conversation loop.

    Attributes:
        status: Current status (active/finished/error/timeout)
        iteration: Current iteration count
        max_iterations: Maximum allowed iterations
        start_time: When the conversation started
        should_terminate: Whether to terminate the loop
        termination_reason: Why termination was requested
        tool_call_history: All tool calls made
    """
    status: str = "active"  # active/finished/error/timeout/user_interrupted
    iteration: int = 0
    max_iterations: int = 10
    start_time: float = field(default_factory=time.time)
    timeout_seconds: float = 300.0  # 5 minutes default
    should_terminate: bool = False
    termination_reason: Optional[str] = None
    tool_call_history: list[ToolCallEntry] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time since start."""
        return time.time() - self.start_time

    @property
    def is_timed_out(self) -> bool:
        """Check if conversation has timed out."""
        return self.elapsed_seconds >= self.timeout_seconds

    @property
    def is_finished(self) -> bool:
        """Check if conversation should stop."""
        return (
            self.should_terminate or
            self.is_timed_out or
            self.iteration >= self.max_iterations or
            self.status in ("finished", "error", "timeout", "user_interrupted")
        )

    def increment_iteration(self) -> None:
        """Increment iteration count and check limits."""
        self.iteration += 1
        if self.iteration >= self.max_iterations:
            self.should_terminate = True
            self.termination_reason = f"Reached max iterations ({self.max_iterations})"

    def mark_finished(self, reason: str = "Task completed") -> None:
        """Mark conversation as finished."""
        self.status = "finished"
        self.should_terminate = True
        self.termination_reason = reason

    def mark_error(self, error: str) -> None:
        """Mark conversation as errored."""
        self.status = "error"
        self.should_terminate = True
        self.termination_reason = error

    def mark_timeout(self) -> None:
        """Mark conversation as timed out."""
        self.status = "timeout"
        self.should_terminate = True
        self.termination_reason = f"Timeout after {self.elapsed_seconds:.1f} seconds"

    def mark_user_interrupted(self) -> None:
        """Mark conversation as interrupted by user."""
        self.status = "user_interrupted"
        self.should_terminate = True
        self.termination_reason = "User interrupted"

    def add_tool_call(self, entry: ToolCallEntry) -> None:
        """Add a tool call to history."""
        entry.iteration = self.iteration
        self.tool_call_history.append(entry)

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        return {
            "status": self.status,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "start_time": self.start_time,
            "timeout_seconds": self.timeout_seconds,
            "should_terminate": self.should_terminate,
            "termination_reason": self.termination_reason,
            "tool_call_history": [
                {
                    "tool_name": e.tool_name,
                    "arguments": e.arguments,
                    "start_time": e.start_time,
                    "end_time": e.end_time,
                    "success": e.success,
                    "result": e.result[:500] if e.result else None,
                    "error": e.error,
                    "iteration": e.iteration
                }
                for e in self.tool_call_history
            ]
        }


@dataclass
class AgentContext:
    """Main context container for the agent.

    This is the unified context structure used by all three agents.

    Attributes:
        short_term_memory: Current conversation messages
        long_term_memory: Persistent memory across sessions
        state: Conversation loop state
        total_tokens_used: Total tokens consumed
        token_budget: Maximum allowed tokens
        session_id: Unique session identifier
        created_at: When this context was created
        metadata: Additional metadata
    """
    # Memory management
    short_term_memory: list[ContextMessage] = field(default_factory=list)
    long_term_memory: list[ContextMessage] = field(default_factory=list)

    # State tracking
    state: ConversationState = field(default_factory=ConversationState)

    # Token budget
    total_tokens_used: int = 0
    token_budget: int = 128000

    # Context window settings (200K tokens = 200 * 1024)
    max_context_window: int = 200 * 1024
    compress_threshold: float = 0.7

    # Metadata
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    @property
    def messages(self) -> list[dict]:
        """Backward compatibility: return simple messages list."""
        return [msg.to_dict() for msg in self.short_term_memory]

    def get_messages_for_api(self) -> list[dict]:
        """Get messages in the format expected by model APIs."""
        return self.messages

    def __getitem__(self, idx: int) -> ContextMessage:
        """Allow indexing into short_term_memory."""
        return self.short_term_memory[idx]

    def __len__(self) -> int:
        """Return number of messages in short_term_memory."""
        return len(self.short_term_memory)

    def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
        token_count: int = None
    ) -> ContextMessage:
        """Add a message to short_term_memory.

        Note: token_count is optional but recommended. If not provided,
        the message will still be added but won't contribute to token statistics.
        """
        if token_count is None:
            token_count = 0
            # Note: In production, consider using a tokenizer to calculate token_count
            # when it's not provided, to ensure accurate tracking.
        msg = ContextMessage(
            role=role,
            content=content,
            metadata=metadata or {},
            token_count=token_count
        )
        self.short_term_memory.append(msg)
        self.total_tokens_used += token_count
        return msg

    def add_user_message(self, content: str, **kwargs) -> ContextMessage:
        """Add a user message."""
        return self.add_message("user", content, **kwargs)

    def add_assistant_message(self, content: str, **kwargs) -> ContextMessage:
        """Add an assistant message."""
        return self.add_message("assistant", content, **kwargs)

    def add_system_message(self, content: str, **kwargs) -> ContextMessage:
        """Add a system message."""
        return self.add_message("system", content, **kwargs)

    def add_tool_message(self, content: str, tool_name: str, **kwargs) -> ContextMessage:
        """Add a tool result message."""
        metadata = kwargs.pop("metadata", {})
        metadata["tool_name"] = tool_name
        return self.add_message("user", content, metadata=metadata, **kwargs)

    def clear(self) -> None:
        """Clear short_term_memory and reset state."""
        self.short_term_memory = []
        self.state = ConversationState()
        self.total_tokens_used = 0

    def calculate_total_tokens(self, messages: list[dict] = None) -> int:
        """Calculate total tokens for current messages or provided messages.

        Args:
            messages: Optional list of message dicts. If None, uses short_term_memory.

        Returns:
            Total token count
        """
        from src.utils.tokenizer import count_messages_tokens

        if messages is None:
            messages = [msg.to_dict() for msg in self.short_term_memory]

        total = count_messages_tokens(messages)
        return total

    def should_compress(self, current_tokens: int = None) -> bool:
        """Check if context should be compressed based on threshold.

        Args:
            current_tokens: Token count. If None, will calculate automatically.

        Returns:
            True if compression is recommended (>= 70% of max_context_window)
        """
        if current_tokens is None:
            current_tokens = self.calculate_total_tokens()

        threshold_tokens = int(self.max_context_window * self.compress_threshold)
        return current_tokens >= threshold_tokens

    def get_compression_ratio(self, current_tokens: int = None) -> float:
        """Get current usage ratio of context window.

        Args:
            current_tokens: Token count. If None, will calculate automatically.

        Returns:
            Ratio (0.0 to 1.0+) of context window usage
        """
        if current_tokens is None:
            current_tokens = self.calculate_total_tokens()

        return current_tokens / self.max_context_window

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        return {
            "short_term_memory": [m.to_full_dict() for m in self.short_term_memory],
            "long_term_memory": [m.to_full_dict() for m in self.long_term_memory],
            "state": self.state.to_dict(),
            "total_tokens_used": self.total_tokens_used,
            "token_budget": self.token_budget,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "metadata": self.metadata
        }


# Convenience factory functions
def create_context(
    max_iterations: int = 10,
    timeout_seconds: float = 300.0,
    token_budget: int = 128000
) -> AgentContext:
    """Create a new AgentContext with custom limits."""
    ctx = AgentContext(token_budget=token_budget)
    ctx.state.max_iterations = max_iterations
    ctx.state.timeout_seconds = timeout_seconds
    return ctx


def from_messages_list(messages: list[dict]) -> AgentContext:
    """Create AgentContext from existing simple messages list."""
    ctx = AgentContext()
    for msg in messages:
        ctx.short_term_memory.append(ContextMessage.from_dict(msg))
    return ctx


__all__ = [
    "ContextMessage",
    "ToolCallEntry",
    "ConversationState",
    "AgentContext",
    "create_context",
    "from_messages_list",
]