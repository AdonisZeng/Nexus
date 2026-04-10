"""
Tool Result Micro-Compactor - Tier 2 of context compression system.

Micro-compacts tool results: keeps recent N intact, replaces older
long results with placeholders.

Supports dual message formats:
- list[dict] (AgentSession.messages format)
- list[ContextMessage] (AgentContext.short_term_memory format)
"""
from typing import Union, List, Optional, Protocol

# Placeholder text for compacted tool results
TOOL_RESULT_PLACEHOLDER = "[Earlier tool result compacted. Re-run the tool if you need full detail.]"
MICRO_COMPACT_THRESHOLD = 120  # chars > this threshold get compacted


class _MessageAdapter(Protocol):
    """Protocol for adapting different message formats."""

    def get_role(self, msg) -> str: ...
    def get_content(self, msg) -> str: ...
    def set_content(self, msg, content: str) -> None: ...
    def is_tool_result(self, msg) -> bool: ...


class DictMessageAdapter:
    """Adapter for list[dict] format (AgentSession.messages)."""

    @staticmethod
    def get_role(msg: dict) -> str:
        return msg.get("role", "user")

    @staticmethod
    def get_content(msg: dict) -> str:
        content = msg.get("content", "")
        if isinstance(content, list):
            return str(content)
        return content

    @staticmethod
    def set_content(msg: dict, content: str) -> None:
        msg["content"] = content

    @staticmethod
    def is_tool_result(msg: dict) -> bool:
        return msg.get("role") == "tool"


class ContextMessageAdapter:
    """Adapter for list[ContextMessage] format (AgentContext.short_term_memory)."""

    @staticmethod
    def get_role(msg) -> str:
        return msg.role

    @staticmethod
    def get_content(msg) -> str:
        return msg.content

    @staticmethod
    def set_content(msg, content: str) -> None:
        msg.content = content

    @staticmethod
    def is_tool_result(msg) -> bool:
        return msg.role == "tool"


class MicroCompactor:
    """
    Micro-compacts tool results in a message list.

    Keeps recent N tool results intact, replaces older ones > threshold
    with a placeholder message.
    """

    def __init__(self, config: Optional[MicroCompactConfig] = None):
        self.config = config or MicroCompactConfig()

    def compact(
        self,
        messages: Union[List[dict], list],
        keep_recent: int = 3,
        adapter=None
    ) -> Union[List[dict], list]:
        """
        Micro-compact tool results in the message list.

        Args:
            messages: Message list (either list[dict] or list[ContextMessage])
            keep_recent: Number of recent tool results to keep intact
            adapter: Message adapter. Auto-detected if not provided.

        Returns:
            The same message list (modified in place), for chaining
        """
        if not messages:
            return messages

        if adapter is None:
            adapter = self._detect_adapter(messages)

        # Single pass: compact older tool results in-place
        tool_count = 0
        for i, msg in enumerate(messages):
            if adapter.is_tool_result(msg):
                tool_count += 1
                # Only compact if we have more than keep_recent and this one is old
                if tool_count > keep_recent:
                    content = adapter.get_content(msg)
                    if len(content) > self.config.compact_threshold:
                        adapter.set_content(msg, self.config.placeholder)

        return messages

    def _detect_adapter(self, messages: list):
        """Detect message format and return appropriate adapter."""
        if not messages:
            return DictMessageAdapter

        first_msg = messages[0]
        if isinstance(first_msg, dict):
            return DictMessageAdapter
        else:
            return ContextMessageAdapter


# Module-level convenience function
_default_compactor: Optional[MicroCompactor] = None


def get_compactor() -> MicroCompactor:
    """Get or create the default compactor instance."""
    global _default_compactor
    if _default_compactor is None:
        _default_compactor = MicroCompactor()
    return _default_compactor


def micro_compact_messages(
    messages: Union[List[dict], list],
    keep_recent: int = 3
) -> Union[List[dict], list]:
    """
    Convenience function to micro-compact tool results.

    Args:
        messages: Message list to compact
        keep_recent: Number of recent tool results to keep intact

    Returns:
        Same message list (modified in place)
    """
    compactor = get_compactor()
    return compactor.compact(messages, keep_recent=keep_recent)


__all__ = [
    "MicroCompactor",
    "MicroCompactConfig",
    "TOOL_RESULT_PLACEHOLDER",
    "micro_compact_messages",
    "get_compactor",
]
