"""OpenAI adapter"""
from .base import ModelAdapter
from .formatter import MessageFormatter
from .errors import (
    robust_json_parse,
    validate_openai_response,
    handle_http_errors,
    check_tool_call_parse_errors_and_retry,
)
from typing import Any, List
import os
import json
import httpx


class OpenAIAdapter(ModelAdapter):
    """Adapter for OpenAI models"""

    def __init__(
        self,
        api_key: str = None,
        model: str = "gpt-4o",
        compat: dict = None
    ):
        # Call parent constructor to initialize capabilities
        super().__init__(model=model, compat=compat)

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._client

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        capabilities = self.get_capabilities()
        chat_messages = MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=chat_messages
            )
            result = response.model_dump()
            validate_openai_response(result, context="OpenAI")
            return result["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            handle_http_errors(e, "OpenAI", "API key")
            raise

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        capabilities = self.get_capabilities()
        chat_messages = MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=chat_messages,
                tools=tools
            )
            result = response.model_dump()
            validate_openai_response(result, context="OpenAI")

            message = result["choices"][0]["message"]
            tool_calls = []

            if message.get("tool_calls"):
                for call in message["tool_calls"]:
                    func = call.get("function", {})
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        parsed = robust_json_parse(args)
                        if "__parse_error__" in parsed:
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"raw": args}
                        else:
                            args = parsed
                    tool_calls.append({
                        "name": func.get("name"),
                        "arguments": args,
                        "id": call.get("id")
                    })

            # Check for parse errors and retry if needed
            retry_result = await check_tool_call_parse_errors_and_retry(
                tool_calls,
                lambda: self._chat_with_tool_prompt(messages, tools, system_prompt)
            )
            if retry_result:
                return retry_result

            return message.get("content") or "", tool_calls
        except httpx.HTTPStatusError as e:
            handle_http_errors(e, "OpenAI", "API key")
            raise

    async def chat_with_tools_and_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> "ChatResult":
        """Chat with tools, returning stop_reason (finish_reason) from OpenAI API.

        @return ChatResult with text, tool_calls, and stop_reason
        """
        from .base import ChatResult

        client = self._get_client()

        # Use MessageFormatter for consistent message handling
        capabilities = self.get_capabilities()
        chat_messages = MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=chat_messages,
                tools=tools
            )
            result = response.model_dump()
            validate_openai_response(result, context="OpenAI")

            # Extract finish_reason (OpenAI's stop_reason equivalent)
            finish_reason = result.get("choices", [{}])[0].get("finish_reason")

            message = result["choices"][0]["message"]
            tool_calls = []

            if message.get("tool_calls"):
                for call in message["tool_calls"]:
                    func = call.get("function", {})
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        parsed = robust_json_parse(args)
                        if "__parse_error__" in parsed:
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"raw": args}
                        else:
                            args = parsed
                    tool_calls.append({
                        "name": func.get("name"),
                        "arguments": args,
                        "id": call.get("id")
                    })

            # Check for parse errors and retry if needed
            retry_result = await check_tool_call_parse_errors_and_retry(
                tool_calls,
                lambda: self._chat_with_tool_prompt(messages, tools, system_prompt)
            )
            if retry_result:
                return ChatResult(text=retry_result[0], tool_calls=retry_result[1], stop_reason=finish_reason)

            return ChatResult(text=message.get("content") or "", tool_calls=tool_calls, stop_reason=finish_reason)
        except httpx.HTTPStatusError as e:
            handle_http_errors(e, "OpenAI", "API key")
            raise

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
        return self.model or "gpt-4o"

    def supports_streaming(self) -> bool:
        return True