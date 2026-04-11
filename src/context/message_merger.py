"""
Message Merger - Lightweight merging of consecutive same-role messages.

Merges consecutive messages with the same role to reduce token usage.
This is a lightweight alternative to full message compaction.

Reference: doc/ToolUse.py normalize_messages() message merging pattern
"""
from dataclasses import dataclass
from typing import Union, List, Optional, Protocol


@dataclass
class MessageMergerConfig:
    """Configuration for message merging."""
    merge_user: bool = True
    merge_assistant: bool = True
    merge_system: bool = False  # Generally don't merge system messages
    merge_tool: bool = True  # Merge consecutive tool results
    max_consecutive_merges: int = 10  # Prevent unlimited merging


class _MessageAdapter(Protocol):
    """Protocol for adapting different message formats."""

    def get_role(self, msg) -> str: ...
    def get_content(self, msg) -> str: ...
    def set_content(self, msg, content) -> None: ...
    def has_tool_calls(self, msg) -> bool: ...


class _DictMessageAdapter:
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
    def set_content(msg: dict, content) -> None:
        msg["content"] = content

    @staticmethod
    def has_tool_calls(msg: dict) -> bool:
        return "tool_calls" in msg


class _ContextMessageAdapter:
    """Adapter for list[ContextMessage] format."""

    @staticmethod
    def get_role(msg) -> str:
        return msg.role

    @staticmethod
    def get_content(msg) -> str:
        return msg.content

    @staticmethod
    def set_content(msg, content) -> None:
        msg.content = content

    @staticmethod
    def has_tool_calls(msg) -> bool:
        return hasattr(msg, 'tool_calls') and msg.tool_calls


class MessageMerger:
    """
    Merges consecutive same-role messages to reduce token usage.

    Merges are done in-place on the message list.
    Supports dual message formats (list[dict] and list[ContextMessage]).
    """

    def __init__(self, config: Optional[MessageMergerConfig] = None):
        self.config = config or MessageMergerConfig()

    def merge(self, messages: Union[List[dict], list]) -> Union[List[dict], list]:
        """Merge consecutive same-role messages.

        Args:
            messages: Message list to merge

        Returns:
            Same message list (modified in place)
        """
        if not messages or len(messages) < 2:
            return messages

        adapter = self._detect_adapter(messages)
        return self._merge_messages(list(messages), adapter)

    def _detect_adapter(self, messages):
        """Detect message format and return appropriate adapter."""
        if not messages:
            return _DictMessageAdapter

        first_msg = messages[0]
        if isinstance(first_msg, dict):
            return _DictMessageAdapter
        else:
            return _ContextMessageAdapter

    def _should_merge_role(self, role: str) -> bool:
        """Check if a role should be merged based on config."""
        if role == "system" and not self.config.merge_system:
            return False
        if role == "user" and not self.config.merge_user:
            return False
        if role == "assistant" and not self.config.merge_assistant:
            return False
        if role == "tool" and not self.config.merge_tool:
            return False
        return True

    def _can_merge(self, prev_msg, curr_msg, adapter) -> bool:
        """Check if two messages can be merged.

        Messages with tool_calls cannot be merged as they have special structure.
        """
        # Don't merge messages that have tool_calls
        if adapter.has_tool_calls(prev_msg) or adapter.has_tool_calls(curr_msg):
            return False
        return True

    def _merge_messages(
        self,
        messages: Union[List[dict], list],
        adapter
    ) -> Union[List[dict], list]:
        """Merge consecutive same-role messages in place."""
        if not messages:
            return messages

        merged = [messages[0]]
        merge_count = 0

        for i in range(1, len(messages)):
            msg = messages[i]
            prev = merged[-1]

            # Check if roles match and should be merged
            if (adapter.get_role(prev) == adapter.get_role(msg) and
                self._should_merge_role(adapter.get_role(msg)) and
                    self._can_merge(prev, msg, adapter) and
                    merge_count < self.config.max_consecutive_merges):

                # Merge content
                prev_content = adapter.get_content(prev)
                curr_content = adapter.get_content(msg)

                # Handle list content (e.g., tool results)
                if isinstance(prev_content, list) and isinstance(curr_content, list):
                    merged_content = prev_content + curr_content
                elif isinstance(prev_content, list):
                    merged_content = prev_content + [curr_content]
                elif isinstance(curr_content, list):
                    merged_content = [prev_content] + curr_content
                else:
                    # Simple string concatenation
                    merged_content = prev_content + "\n" + curr_content

                adapter.set_content(prev, merged_content)
                merge_count += 1
            else:
                # Different role or can't merge, just append
                merged.append(msg)
                merge_count = 0  # Reset count when we add a different message

        # Replace original list in place
        messages.clear()
        messages.extend(merged)

        return messages


# Module-level convenience function
_default_merger: Optional[MessageMerger] = None


def get_merger() -> MessageMerger:
    """Get or create the default merger instance."""
    global _default_merger
    if _default_merger is None:
        _default_merger = MessageMerger()
    return _default_merger


def merge_consecutive_messages(
    messages: Union[List[dict], list],
    merge_user: bool = True,
    merge_assistant: bool = True,
    merge_tool: bool = True,
    merge_system: bool = False,
    max_consecutive_merges: int = 10,
) -> Union[List[dict], list]:
    """
    Convenience function to merge consecutive same-role messages.

    Args:
        messages: Message list to merge
        merge_user: Whether to merge consecutive user messages
        merge_assistant: Whether to merge consecutive assistant messages
        merge_tool: Whether to merge consecutive tool messages
        merge_system: Whether to merge consecutive system messages
        max_consecutive_merges: Maximum number of consecutive merges allowed

    Returns:
        Same message list (modified in place)
    """
    # Use singleton for default config to avoid per-call allocation
    if (merge_user == True and merge_assistant == True and
            merge_tool == True and merge_system == False and
            max_consecutive_merges == 10):
        return get_merger().merge(messages)
    # Custom config requires new instance
    config = MessageMergerConfig(
        merge_user=merge_user,
        merge_assistant=merge_assistant,
        merge_tool=merge_tool,
        merge_system=merge_system,
        max_consecutive_merges=max_consecutive_merges,
    )
    merger = MessageMerger(config)
    return merger.merge(messages)


__all__ = [
    "MessageMerger",
    "MessageMergerConfig",
    "merge_consecutive_messages",
    "get_merger",
]
