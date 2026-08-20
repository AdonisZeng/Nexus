"""Token counting utilities using tiktoken (with fallback to estimation)"""
from typing import List, Optional
import logging

logger = logging.getLogger("Nexus")

# Default encoding - use cl100k_base for GPT-4/Claude compatible encoding
_default_encoding = None
_tiktoken_failed = False


def get_encoding(model: str = "cl100k_base"):
    """Get or create a tiktoken encoding instance.

    Falls back to estimation if tiktoken fails (e.g., network issues).

    Args:
        model: Encoding model name. Options: cl100k_base (default), p50k_base, r50k_base

    Returns:
        tiktoken.Encoding instance or None if unavailable
    """
    global _default_encoding, _tiktoken_failed

    if _tiktoken_failed:
        return None

    if _default_encoding is None:
        try:
            import tiktoken
            _default_encoding = tiktoken.get_encoding(model)
        except Exception as e:
            logger.warning(f"Failed to load tiktoken, using estimation: {e}")
            _tiktoken_failed = True
            return None

    return _default_encoding


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """Count the number of tokens in a text string.

    Args:
        text: Text to count tokens for
        model: Encoding model name (unused if fallback to estimation)

    Returns:
        Number of tokens
    """
    if not text:
        return 0

    encoding = get_encoding(model)
    if encoding:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass

    # Fallback to estimation
    return estimate_tokens(text)


def count_messages_tokens(
    messages: List[dict],
    model: str = "cl100k_base",
    addGPT4Tokens: bool = True
) -> int:
    """Count tokens for a list of messages (OpenAI format).

    This follows OpenAI's token counting approach:
    - Every message has overhead tokens (role, content structure)
    - The total includes a small fixed overhead for the conversation

    Args:
        messages: List of message dicts with 'role' and 'content' keys
        model: Encoding model name
        addGPT4Tokens: Whether to add GPT-4 specific overhead

    Returns:
        Total token count
    """
    if not messages:
        return 0

    encoding = get_encoding(model)

    # Base overhead per message (role + formatting)
    tokens_per_message = 4 if addGPT4Tokens else 3

    # Overall overhead
    base_tokens = 3 if addGPT4Tokens else 2

    total = base_tokens

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        # Count content tokens
        total += count_tokens(content, model)

        # Add role overhead
        total += tokens_per_message

        # Add role name
        total += count_tokens(role, model)

    return total


def estimate_tokens(text: str) -> int:
    """Estimate token count using a simple heuristic.

    This is faster than tiktoken for rough estimates.
    Average: 1 token ≈ 4 characters in English, 2 in Chinese

    Args:
        text: Text to estimate

    Returns:
        Estimated token count (upper bound)
    """
    if not text:
        return 0

    # Simple heuristic: ~4 chars per token for English, ~2 for Chinese
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    english_chars = len(text) - chinese_chars

    return (english_chars // 4) + (chinese_chars // 2) + 1


def truncate_text(text: str, max_tokens: int, model: str = "cl100k_base") -> str:
    """Truncate text to fit within a maximum token limit.

    Args:
        text: Text to truncate
        max_tokens: Maximum tokens allowed
        model: Encoding model name

    Returns:
        Truncated text that fits within the limit
    """
    if not text:
        return text

    encoding = get_encoding(model)
    tokens = encoding.encode(text)

    if len(tokens) <= max_tokens:
        return text

    # Truncate and decode
    truncated_tokens = tokens[:max_tokens]
    return encoding.decode(truncated_tokens)


def ensure_token_count(
    messages: list,
    model: str = "cl100k_base"
) -> int:
    """
    Recalculate and ensure token_count is set for all messages.

    This fixes the issue where ContextMessage.token_count is often 0
    because add_message() doesn't require it.

    Args:
        messages: Message list to process (list[dict] or list[ContextMessage])
        model: Encoding model name

    Returns:
        Total token count for all messages
    """
    total = 0

    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            tokens = count_tokens(content, model)
            total += tokens
        else:
            # ContextMessage or similar dataclass
            if hasattr(msg, "token_count") and msg.token_count == 0 and hasattr(msg, "content"):
                msg.token_count = count_tokens(msg.content, model)
            total += getattr(msg, "token_count", 0)

    return total


def recalculate_message_tokens(
    messages: list,
    model: str = "cl100k_base"
) -> int:
    """
    Recalculate token counts for all messages and update in place.

    Returns:
        Total token count after recalculation
    """
    total = 0

    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            tokens = count_tokens(content, model)
            msg["token_count"] = tokens  # Add token_count to dict if missing
            total += tokens
        elif hasattr(msg, "token_count"):
            # ContextMessage
            content = getattr(msg, "content", "")
            msg.token_count = count_tokens(content, model)
            total += msg.token_count

    return total


__all__ = [
    "get_encoding",
    "count_tokens",
    "count_messages_tokens",
    "estimate_tokens",
    "truncate_text",
    "ensure_token_count",
    "recalculate_message_tokens",
]