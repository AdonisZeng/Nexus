"""Unified message format converter for different providers."""
import json
from typing import List, Optional

# Canonical implementation lives in src.error.json_repair
from src.error.json_repair import (
    decode_html_entities,
    decode_html_entities_in_object,
)

__all__ = [
    "MessageFormatter",
    "decode_html_entities",
    "decode_html_entities_in_object",
]


class MessageFormatter:
    """Unified message format converter for different LLM providers."""

    @staticmethod
    def to_openai(
        messages: List[dict],
        system_prompt: Optional[str] = None,
        supports_developer_role: bool = False
    ) -> List[dict]:
        """Convert internal messages to OpenAI chat-completions format."""
        formatted = []

        if system_prompt:
            role = "developer" if supports_developer_role else "system"
            formatted.append({"role": role, "content": system_prompt})

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system":
                continue

            # Tool result messages
            if role == "tool":
                formatted.append({
                    "role": "tool",
                    "content": msg.get("content", ""),
                    "tool_call_id": msg.get("tool_call_id", "")
                })
                continue

            # Assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                openai_tool_calls = []
                for tc in msg.get("tool_calls", []):
                    args = tc.get("arguments", {})
                    if isinstance(args, dict):
                        args = json.dumps(args)
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
        """Convert internal messages to Anthropic format.

        @return Tuple of (system_prompt, anthropic_messages)
        """
        anthropic_messages = []

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system":
                system_prompt = msg.get("content")
                continue

            # Tool result messages
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

            # Assistant messages with tool_calls
            if role == "assistant" and msg.get("tool_calls"):
                content_blocks = []

                if msg.get("content"):
                    content_blocks.append({
                        "type": "text",
                        "text": msg.get("content")
                    })

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
        """Convert internal messages to Ollama /api/chat format (plain roles)."""
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
