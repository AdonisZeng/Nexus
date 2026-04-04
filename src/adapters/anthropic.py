"""Anthropic Claude adapter"""
from .base import ModelAdapter, StreamEvent, StreamEventType
from .formatter import MessageFormatter
from .errors import check_tool_call_parse_errors_and_retry
from typing import Any, List, AsyncIterator
import os
import logging

logger = logging.getLogger("Nexus")


class AnthropicAdapter(ModelAdapter):
    """Adapter for Anthropic Claude models"""

    def __init__(
        self,
        api_key: str = None,
        model: str = "claude-sonnet-4-20250514",
        compat: dict = None
    ):
        # Call parent constructor to initialize capabilities
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

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=16384,
            system=system_prompt,
            messages=anthropic_messages
        )

        # Validate response has content
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
        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        # Convert tools to Anthropic format
        anthropic_tools = []
        for tool in tools:
            anthropic_tools.append({
                "name": tool.get("name"),
                "description": tool.get("description"),
                "input_schema": tool.get("input_schema", {})
            })

        response = client.messages.create(
            model=self.model,
            max_tokens=16384,
            system=system_prompt,
            messages=anthropic_messages,
            tools=anthropic_tools
        )

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
            return retry_result

        # Extract text response - find first text block, not assuming position
        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
                break
        return text, tool_calls

    async def chat_with_tools_and_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> "ChatResult":
        """Chat with tools, returning stop_reason from Anthropic API.

        @return ChatResult with text, tool_calls, and stop_reason
        """
        from .base import ChatResult

        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        # Convert tools to Anthropic format
        anthropic_tools = []
        for tool in tools:
            anthropic_tools.append({
                "name": tool.get("name"),
                "description": tool.get("description"),
                "input_schema": tool.get("input_schema", {})
            })

        response = client.messages.create(
            model=self.model,
            max_tokens=16384,
            system=system_prompt,
            messages=anthropic_messages,
            tools=anthropic_tools
        )

        # Extract stop_reason from response
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
            return ChatResult(text=retry_result[0], tool_calls=retry_result[1], stop_reason=stop_reason)

        # Extract text response - find first text block, not assuming position
        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
                break

        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=stop_reason)

    async def _chat_with_tool_prompt(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Fallback: use prompt injection for tool calling."""
        tool_prompt = self._build_tool_prompt(tools)
        enhanced_prompt = f"{system_prompt}\n\n{tool_prompt}" if system_prompt else tool_prompt

        response = await self.chat(messages, enhanced_prompt)
        tool_calls = self._parse_tool_calls_from_response(response)

        return response, tool_calls

    def _build_tool_prompt(self, tools: List[dict]) -> str:
        """Build tool prompt for prompt injection."""
        tool_descriptions = []
        for tool in tools:
            schema = tool.get("input_schema", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])

            params_desc = []
            for name, prop in props.items():
                req = " (required)" if name in required else " (optional)"
                params_desc.append(f"    - {name}{req}: {prop.get('description', prop.get('type', 'any'))}")

            tool_descriptions.append(f"""
- {tool.get('name', 'unnamed_tool')}: {tool.get('description', 'No description')}
  Parameters:
{chr(10).join(params_desc) if params_desc else '  (no parameters)'}
""")

        return f"""
You have access to the following tools. To use a tool, respond with XML format:

<tool_call name="tool_name">
{{"param1": "value1", "param2": "value2"}}
</tool_call>

You can make multiple tool calls by using multiple <tool_call> blocks.

Available tools:
{''.join(tool_descriptions)}
"""

    def _parse_tool_calls_from_response(self, response: str) -> List[dict]:
        """Parse tool calls from model response."""
        import re
        import json

        tool_calls = []
        pattern = r'<tool_call\s+name="([^"]+)">\s*([\s\S]*?)\s*</tool_call>'

        for match in re.finditer(pattern, response):
            name = match.group(1)
            args_str = match.group(2).strip()
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {"raw": args_str}
            tool_calls.append({
                "name": name,
                "arguments": args,
                "id": f"prompt_{len(tool_calls)}",
            })

        return tool_calls

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
        """Streaming chat with tool support using Anthropic's stream API.

        Note: For tool calls, we use a hybrid approach - stream the text response
        in real-time, then get tool calls from a non-streaming call after the
        text is complete. This ensures we get complete tool call data while
        still providing real-time text feedback to the user.
        """
        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        # Convert tools to Anthropic format
        anthropic_tools = []
        if tools:
            for tool in tools:
                anthropic_tools.append({
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "input_schema": tool.get("input_schema", {})
                })

        # Collect text content from streaming
        text_chunks = []
        tool_calls_buffer = []  # 暂存 tool_use 块
        current_tool_use = None  # 当前正在收集的 tool_use
        stop_reason = None

        # Use streaming API for text
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
                    # 开始一个新的内容块
                    content_block = event.content_block
                    if content_block.type == "tool_use":
                        # 开始收集 tool_use
                        current_tool_use = {
                            "name": content_block.name,
                            "id": content_block.id,
                            "input": ""
                        }

                elif event_type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_chunks.append(delta.text)
                        yield StreamEvent(
                            type=StreamEventType.TEXT_DELTA,
                            content=delta.text
                        )
                    elif delta.type == "tool_use_delta":
                        # 收集 tool_use 参数增量
                        if current_tool_use is not None:
                            current_tool_use["input"] += delta.input_json

                elif event_type == "content_block_stop":
                    # 内容块结束
                    if current_tool_use is not None:
                        # 完成当前 tool_use 的收集
                        import json
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
                    # 从 message_delta 中提取 stop_reason
                    if hasattr(event, 'delta') and hasattr(event.delta, 'stop_reason'):
                        stop_reason = event.delta.stop_reason

                elif event_type == "message_stop":
                    yield StreamEvent(
                        type=StreamEventType.MESSAGE_STOP,
                        stop_reason=stop_reason
                    )

        # After streaming is done, emit tool calls if any (from streaming buffer)
        if tools and tool_calls_buffer:
            # Streaming already provided tool_calls, no need for fallback
            pass
        elif tools:
            # Fallback: streaming didn't provide tool_calls, use non-streaming call
            # This handles cases where tool_use_delta events might not have been received
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=16384,
                    system=system_prompt,
                    messages=anthropic_messages,
                    tools=anthropic_tools
                )

                tool_calls = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_calls.append({
                            "name": block.name,
                            "arguments": block.input,
                            "id": block.id
                        })

                # Emit tool calls if any
                if tool_calls:
                    yield StreamEvent(
                        type=StreamEventType.TOOL_USE_COMPLETE,
                        tool_calls=tool_calls
                    )
            except Exception as e:
                logger.warning(f"Failed to get tool calls: {e}")