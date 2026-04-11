"""
Tool Use Normalizer - Detects and handles orphaned tool_use blocks.

Detects tool_use blocks in assistant messages that have no corresponding
tool_result, and inserts placeholder results to prevent API errors.

Reference: doc/ToolUse.py normalize_messages() pattern
"""
from dataclasses import dataclass
from typing import Union, List, Set, Optional, Protocol


# Default placeholder for orphaned tool results
ORPHANED_RESULT_PLACEHOLDER = "(tool execution failed or was cancelled)"


@dataclass
class ToolUseNormalizerConfig:
    """Configuration for tool use normalization."""
    orphaned_result_placeholder: str = ORPHANED_RESULT_PLACEHOLDER
    enabled: bool = True


class _MessageAdapter(Protocol):
    """Protocol for adapting different message formats."""

    def get_role(self, msg) -> str: ...
    def get_content(self, msg) -> str: ...
    def get_tool_calls(self, msg) -> List[dict]: ...
    def add_message(self, messages, msg) -> None: ...
    def get_tool_call_id(self, msg) -> str: ...
    def get_type(self, block) -> str: ...
    def get_block_id(self, block) -> str: ...


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
    def get_tool_calls(msg: dict) -> List[dict]:
        """Extract tool_use blocks from assistant message."""
        if msg.get("role") != "assistant":
            return []
        content = msg.get("content", [])
        if isinstance(content, list):
            return [
                block for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
        return []

    @staticmethod
    def add_message(messages: List[dict], msg: dict) -> None:
        messages.append(msg)

    @staticmethod
    def get_tool_call_id(msg: dict) -> str:
        return msg.get("tool_call_id", "")

    @staticmethod
    def get_type(block: dict) -> str:
        return block.get("type", "")

    @staticmethod
    def get_block_id(block: dict) -> str:
        return block.get("id", block.get("tool_use_id", ""))


class _ContextMessageAdapter:
    """Adapter for list[ContextMessage] format."""

    @staticmethod
    def get_role(msg) -> str:
        return msg.role

    @staticmethod
    def get_content(msg) -> str:
        return msg.content

    @staticmethod
    def get_tool_calls(msg) -> List[dict]:
        """ContextMessage uses different structure, delegate to Dict."""
        return _DictMessageAdapter.get_tool_calls({
            "role": msg.role,
            "content": msg.content if hasattr(msg, 'content') else ""
        })

    @staticmethod
    def add_message(messages, msg) -> None:
        messages.append(msg)

    @staticmethod
    def get_tool_call_id(msg) -> str:
        return getattr(msg, 'tool_call_id', "")

    @staticmethod
    def get_type(block) -> str:
        return block.get("type", "")

    @staticmethod
    def get_block_id(block) -> str:
        return block.get("id", block.get("tool_use_id", ""))


class ToolUseNormalizer:
    """
    Normalizes tool_use/tool_result pairs in message lists.

    Detects orphaned tool_use blocks (assistant messages with tool_calls
    that have no corresponding tool_result) and inserts placeholder results.
    """

    def __init__(self, config: Optional[ToolUseNormalizerConfig] = None):
        self.config = config or ToolUseNormalizerConfig()

    def normalize(self, messages: Union[List[dict], list]) -> Union[List[dict], list]:
        """Normalize tool uses in message list, handling orphans.

        Args:
            messages: Message list to normalize

        Returns:
            Same message list with orphaned tool_uses fixed
        """
        if not self.config.enabled:
            return messages

        if not messages:
            return messages

        adapter = self._detect_adapter(messages)

        # Step 1: Collect all tool_result IDs
        existing_results = self._collect_tool_result_ids(messages, adapter)

        # Step 2: Find and fix orphaned tool_uses
        return self._insert_orphaned_results(messages, existing_results, adapter)

    def _detect_adapter(self, messages):
        """Detect message format and return appropriate adapter."""
        if not messages:
            return _DictMessageAdapter

        first_msg = messages[0]
        if isinstance(first_msg, dict):
            return _DictMessageAdapter
        else:
            return _ContextMessageAdapter

    def _collect_tool_result_ids(
        self,
        messages: Union[List[dict], list],
        adapter
    ) -> Set[str]:
        """Collect all tool_call_ids that have corresponding tool_results."""
        existing = set()
        for msg in messages:
            if adapter.get_role(msg) == "tool":
                tool_call_id = adapter.get_tool_call_id(msg)
                if tool_call_id:
                    existing.add(tool_call_id)
        return existing

    def _insert_orphaned_results(
        self,
        messages: Union[List[dict], list],
        existing_results: Set[str],
        adapter
    ) -> Union[List[dict], list]:
        """Find orphaned tool_uses and insert placeholder results."""
        orphaned_found = []

        for msg in messages:
            if adapter.get_role(msg) != "assistant":
                continue

            tool_calls = adapter.get_tool_calls(msg)
            if not tool_calls:
                continue

            for block in tool_calls:
                block_id = adapter.get_block_id(block)
                if block_id and block_id not in existing_results:
                    orphaned_found.append(block_id)

        # Insert placeholder results for orphaned tool_uses
        for tool_use_id in orphaned_found:
            placeholder_msg = {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": self.config.orphaned_result_placeholder
                }]
            }
            adapter.add_message(messages, placeholder_msg)

        return messages


# Module-level convenience function
_default_normalizer: Optional[ToolUseNormalizer] = None


def get_normalizer() -> ToolUseNormalizer:
    """Get or create the default normalizer instance."""
    global _default_normalizer
    if _default_normalizer is None:
        _default_normalizer = ToolUseNormalizer()
    return _default_normalizer


def normalize_tool_uses(
    messages: Union[List[dict], list],
    placeholder: str = ORPHANED_RESULT_PLACEHOLDER
) -> Union[List[dict], list]:
    """
    Convenience function to normalize orphaned tool_uses.

    Args:
        messages: Message list to normalize
        placeholder: Text for orphaned tool results (uses singleton if default)

    Returns:
        Same message list (modified in place)
    """
    # Use singleton for default placeholder to avoid per-call allocation
    if placeholder == ORPHANED_RESULT_PLACEHOLDER:
        return get_normalizer().normalize(messages)
    # Custom placeholder requires new instance
    config = ToolUseNormalizerConfig(orphaned_result_placeholder=placeholder)
    normalizer = ToolUseNormalizer(config)
    return normalizer.normalize(messages)


__all__ = [
    "ToolUseNormalizer",
    "ToolUseNormalizerConfig",
    "ORPHANED_RESULT_PLACEHOLDER",
    "normalize_tool_uses",
    "get_normalizer",
]
