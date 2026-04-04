"""Unified message format converter for different providers."""
import re
from typing import List, Optional, Any


# HTML entity pattern for decoding
HTML_ENTITY_PATTERN = re.compile(
    r'&(?:amp|lt|gt|quot|apos|#39|#x[0-9a-fA-F]+|#\d+);'
)


def decode_html_entities(value: str) -> str:
    """Decode HTML entities to original characters.

    Supported entities:
    - &amp; → &
    - &quot; → "
    - &#39; / &apos; → '
    - &lt; → <
    - &gt; → >
    - &#xHH; → hex character
    - &#DD; → decimal character

    @param value: String containing HTML entities
    @return: Decoded string
    """
    def replace_entity(match: re.Match) -> str:
        entity = match.group(0)

        # Named entities
        replacements = {
            '&amp;': '&',
            '&quot;': '"',
            '&#39;': "'",
            '&apos;': "'",
            '&lt;': '<',
            '&gt;': '>',
        }

        if entity in replacements:
            return replacements[entity]

        # Hex entity &#xHH;
        hex_match = re.match(r'&#x([0-9a-fA-F]+);', entity)
        if hex_match:
            code_point = int(hex_match.group(1), 16)
            return chr(code_point)

        # Decimal entity &#DD;
        dec_match = re.match(r'&#(\d+);', entity)
        if dec_match:
            code_point = int(dec_match.group(1))
            return chr(code_point)

        # Unknown entity, keep as-is
        return entity

    return HTML_ENTITY_PATTERN.sub(replace_entity, value)


def decode_html_entities_in_object(obj: Any) -> Any:
    """Recursively decode HTML entities in all strings within an object.

    @param obj: Arbitrary Python object
    @return: Object with decoded strings
    """
    if isinstance(obj, str):
        return decode_html_entities(obj)

    if isinstance(obj, list):
        return [decode_html_entities_in_object(item) for item in obj]

    if isinstance(obj, dict):
        return {
            k: decode_html_entities_in_object(v)
            for k, v in obj.items()
        }

    return obj


class MessageFormatter:
    """Unified message format converter for different LLM providers."""

    @staticmethod
    def to_openai(
        messages: List[dict],
        system_prompt: Optional[str] = None,
        supports_developer_role: bool = False
    ) -> List[dict]:
        """
        Convert messages to OpenAI format.

        Args:
            messages: List of messages in internal format
            system_prompt: System prompt
            supports_developer_role: Whether the model supports developer role

        Returns:
            Messages in OpenAI format
        """
        formatted = []

        # Add system prompt
        if system_prompt:
            role = "developer" if supports_developer_role else "system"
            formatted.append({"role": role, "content": system_prompt})

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system":
                continue

            # Handle tool result messages
            if role == "tool":
                formatted.append({
                    "role": "tool",
                    "content": msg.get("content", ""),
                    "tool_call_id": msg.get("tool_call_id", "")
                })
                continue

            # Handle assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                # Convert internal tool_calls format to OpenAI format
                openai_tool_calls = []
                for tc in msg.get("tool_calls", []):
                    args = tc.get("arguments", {})
                    if isinstance(args, dict):
                        args = __import__("json").dumps(args)
                    openai_tool_calls.append({
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": args
                        }
                    })
                
                formatted.append({
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": openai_tool_calls
                })
                continue

            # Regular messages
            formatted.append({
                "role": role,
                "content": msg.get("content", "")
            })

        return formatted

    @staticmethod
    def to_anthropic(
        messages: List[dict],
        system_prompt: Optional[str] = None,
        tools: Optional[List[dict]] = None
    ) -> tuple[str, List[dict]]:
        """
        Convert messages to Anthropic format.

        Args:
            messages: List of messages in internal format
            system_prompt: System prompt (will be updated if found in messages)
            tools: Tools schema (for potential future use)

        Returns:
            Tuple of (system_prompt, anthropic_messages)
        """
        anthropic_messages = []

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system":
                system_prompt = msg.get("content")
                continue

            # Handle tool result messages
            if role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", "")
                    }]
                })
                continue

            # Handle assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                content_blocks = []
                
                # Add text content if present
                if msg.get("content"):
                    content_blocks.append({
                        "type": "text",
                        "text": msg.get("content")
                    })
                
                # Add tool_use blocks
                for tool_call in msg.get("tool_calls", []):
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tool_call.get("id", ""),
                        "name": tool_call.get("name", ""),
                        "input": tool_call.get("arguments", {})
                    })
                
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content_blocks if content_blocks else [{"type": "text", "text": ""}]
                })
                continue

            # Regular messages
            anthropic_messages.append({
                "role": role,
                "content": msg.get("content", "")
            })

        return system_prompt, anthropic_messages

    @staticmethod
    def to_ollama(
        messages: List[dict],
        system_prompt: Optional[str] = None
    ) -> List[dict]:
        """
        Convert messages to Ollama format.

        Args:
            messages: List of messages in internal format
            system_prompt: System prompt

        Returns:
            Messages in Ollama format
        """
        formatted = []

        if system_prompt:
            formatted.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.get("role", "user")
            if role != "system":
                formatted.append({
                    "role": role,
                    "content": msg.get("content", "")
                })

        return formatted

    @staticmethod
    def to_lmstudio(
        messages: List[dict],
        system_prompt: Optional[str] = None,
        supports_developer_role: bool = False
    ) -> List[dict]:
        """
        Convert messages to LMStudio (OpenAI-compatible) format.

        LMStudio uses OpenAI-compatible API, but most local models
        don't support the developer role.

        Args:
            messages: List of messages in internal format
            system_prompt: System prompt
            supports_developer_role: Whether the model supports developer role

        Returns:
            Messages in OpenAI format for LMStudio
        """
        # Use system role for LMStudio (most local models don't support developer)
        return MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=False  # Force false for LMStudio
        )