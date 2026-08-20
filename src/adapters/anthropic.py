"""Anthropic Claude adapter"""
from typing import List, AsyncIterator
import os
import logging

from .base import ModelAdapter, ChatResult, StreamEvent, StreamEventType
from .formatter import MessageFormatter
from .errors import check_tool_call_parse_errors_and_retry

logger = logging.getLogger("Nexus")


class AnthropicAdapter(ModelAdapter):
    """Adapter for Anthropic Claude models"""

    PROVIDER_NAME = "anthropic"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            api_key=config.get("api_key"),
            model=config.get("model", "claude-sonnet-4-20250514"),
            compat=config.get("compat"),
        )

    def __init__(
        self,
        api_key: str = None,
        model: str = "claude-sonnet-4-20250514",
        compat: dict = None
    ):
        super().__init__(model=model, compat=compat)

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("anthropic package not installed. Run: pip install anthropic")
        return self._client

    @staticmethod
    def _convert_tools(tools: List[dict]) -> List[dict]:
        return [
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "input_schema": tool.get("input_schema", {})
            }
            for tool in tools
        ]

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        client = self._get_client()

        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=16384,
            system=system_prompt,
            messages=anthropic_messages
        )

        if not response.content:
            logger.error("[Anthropic] API 响应格式错误: missing content")
            raise ValueError("Invalid API response: missing content")

        return response.content[0].text

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        result = await self.chat_with_tools_and_stop_reason(messages, tools, system_prompt)
        return result.text, result.tool_calls

    async def chat_with_tools_and_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> ChatResult:
        """Chat with tools, returning stop_reason from Anthropic API."""
        client = self._get_client()

        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=16384,
            system=system_prompt,
            messages=anthropic_messages,
            tools=self._convert_tools(tools)
        )

        stop_reason = getattr(response, 'stop_reason', None)

        # Extract tool calls with exception handling
        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    arguments = block.input
                except Exception as e:
                    logger.warning(f"[Anthropic] tool_use input 解析异常: {e}")
                    arguments = {"__parse_error__": str(e)}
                tool_calls.append({
                    "name": block.name,
                    "arguments": arguments,
                    "id": block.id
                })

        # Check for parse errors and retry if needed
        retry_result = await check_tool_call_parse_errors_and_retry(
            tool_calls,
            lambda: self._chat_with_tool_prompt(messages, tools, system_prompt)
        )
        if retry_result:
            return ChatResult(
                text=retry_result[0], tool_calls=retry_result[1], stop_reason=stop_reason
            )

        # Extract text response - find first text block, not assuming position
        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
                break

        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=stop_reason)

    def get_name(self) -> str:
        return self.model or "claude-sonnet-4-20250514"

    def supports_streaming(self) -> bool:
        return True

    async def chat_stream(
        self,
        messages: List[dict],
        tools: List[dict] = None,
        system_prompt: str = None
    ) -> AsyncIterator[StreamEvent]:
        """Real streaming via Anthropic's stream API (text deltas in real time,
        tool calls collected from tool_use_delta events)."""
        import json

        client = self._get_client()

        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        anthropic_tools = self._convert_tools(tools) if tools else []

        tool_calls_buffer = []
        current_tool_use = None
        stop_reason = None

        with client.messages.stream(
            model=self.model,
            max_tokens=16384,
            system=system_prompt,
            messages=anthropic_messages,
            tools=anthropic_tools
        ) as stream:
            for event in stream:
                event_type = event.type

                if event_type == "content_block_start":
                    content_block = event.content_block
                    if content_block.type == "tool_use":
                        current_tool_use = {
                            "name": content_block.name,
                            "id": content_block.id,
                            "input": ""
                        }

                elif event_type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA,
                            content=delta.text
                        )
                    elif delta.type == "tool_use_delta":
                        if current_tool_use is not None:
                            current_tool_use["input"] += delta.input_json

                elif event_type == "content_block_stop":
                    if current_tool_use is not None:
                        try:
                            args = json.loads(current_tool_use["input"]) if current_tool_use["input"] else {}
                        except json.JSONDecodeError:
                            args = {"__raw__": current_tool_use["input"]}
                        tool_calls_buffer.append({
                            "name": current_tool_use["name"],
                            "id": current_tool_use["id"],
                            "arguments": args
                        })
                        current_tool_use = None

                elif event_type == "message_delta":
                    if hasattr(event, 'delta') and hasattr(event.delta, 'stop_reason'):
                        stop_reason = event.delta.stop_reason

                elif event_type == "message_stop":
                    yield StreamEvent(
                        type=StreamEventType.MESSAGE_STOP,
                        stop_reason=stop_reason
                    )

        # Emit tool calls collected during streaming
        if tool_calls_buffer:
            yield StreamEvent(
                type=StreamEventType.TOOL_USE_COMPLETE,
                tool_calls=tool_calls_buffer
            )
        elif tools:
            # Fallback: streaming didn't provide tool_calls, use non-streaming call
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=16384,
                    system=system_prompt,
                    messages=anthropic_messages,
                    tools=anthropic_tools
                )

                tool_calls = [
                    {"name": block.name, "arguments": block.input, "id": block.id}
                    for block in response.content
                    if block.type == "tool_use"
                ]

                if tool_calls:
                    yield StreamEvent(
                        type=StreamEventType.TOOL_USE_COMPLETE,
                        tool_calls=tool_calls
                    )
            except Exception as e:
                logger.warning(f"Failed to get tool calls: {e}")
