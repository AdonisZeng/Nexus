"""Ollama local model adapter"""
from .base import ModelAdapter
from .formatter import MessageFormatter
from .errors import (
    robust_json_parse,
    handle_http_errors,
    check_tool_call_parse_errors_and_retry,
)
from typing import Any, List
import httpx
import json
import logging

logger = logging.getLogger("Nexus")


class OllamaAdapter(ModelAdapter):
    """Adapter for Ollama local models

    Note: Most Ollama models don't support native tool calling.
    This adapter will try native tool calling first, then fall back
    to prompt injection if enabled.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        compat: dict = None
    ):
        # Call parent constructor to initialize capabilities
        super().__init__(model=model, compat=compat)

        self.base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        return self._client

    def _build_messages(self, messages: List[dict], system_prompt: str = None) -> List[dict]:
        """Build messages in Ollama format"""
        return MessageFormatter.to_ollama(messages, system_prompt)

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        client = self._get_client()

        # Build messages
        chat_messages = self._build_messages(messages, system_prompt)

        response = await client.post(
            "/api/chat",
            json={
                "model": self.model,
                "messages": chat_messages,
                "stream": False
            }
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Chat with tools support"""
        capabilities = self.get_capabilities()

        # Check if model supports tools
        if not capabilities.supports_tools:
            if capabilities.fallback_to_prompt_injection:
                return await self._chat_with_tool_prompt(messages, tools, system_prompt)
            # Simple fallback to chat without tools
            response = await self.chat(messages, system_prompt)
            return response, []

        # Try native tool calling (Ollama 0.1.20+ supports tools)
        try:
            response_text, tool_calls = await self._try_native_tool_call(messages, tools, system_prompt)

            # Check for parse errors and retry if needed
            retry_result = await check_tool_call_parse_errors_and_retry(
                tool_calls,
                lambda: self._chat_with_tool_prompt(messages, tools, system_prompt)
            )
            if retry_result:
                return retry_result

            return response_text, tool_calls
        except Exception as e:
            logger.warning(f"Tool calling failed: {e}, trying prompt injection")
            return await self._chat_with_tool_prompt(messages, tools, system_prompt)

    async def _try_native_tool_call(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Try native tool calling with Ollama"""
        client = self._get_client()

        chat_messages = self._build_messages(messages, system_prompt)

        # Ollama uses a different format for tools
        ollama_tools = self._convert_tools_for_ollama(tools)

        try:
            response = await client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": chat_messages,
                    "tools": ollama_tools,
                    "stream": False
                }
            )
            response.raise_for_status()
            result = response.json()

            message = result.get("message", {})
            tool_calls = self._extract_tool_calls(message)

            return message.get("content") or "", tool_calls

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning(f"Model doesn't support tool calling (400), disabling")
                self._capabilities.supports_tools = False
                raise
            handle_http_errors(e, "Ollama")
            raise

    def _convert_tools_for_ollama(self, tools: List[dict]) -> List[dict]:
        """Convert tools to Ollama format"""
        ollama_tools = []
        for tool in tools:
            ollama_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {})
                }
            })
        return ollama_tools

    def _extract_tool_calls(self, message: dict) -> list[dict]:
        """Extract tool calls from response message"""
        tool_calls = []
        for call in message.get("tool_calls", []):
            func = call.get("function", {})
            args = func.get("arguments", {})
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
                "id": call.get("id", f"ollama_{len(tool_calls)}"),
            })
        return tool_calls

    async def _chat_with_tool_prompt(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Fallback: use prompt injection for tool calling"""
        tool_prompt = self._build_tool_prompt(tools)
        enhanced_prompt = f"{system_prompt}\n\n{tool_prompt}" if system_prompt else tool_prompt

        response = await self.chat(messages, enhanced_prompt)
        tool_calls = self._parse_tool_calls_from_response(response)

        return response, tool_calls

    def _build_tool_prompt(self, tools: List[dict]) -> str:
        """Build tool prompt for prompt injection"""
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
- {tool['name']}: {tool.get('description', 'No description')}
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
        """Parse tool calls from model response"""
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
        return "ollama"

    def supports_streaming(self) -> bool:
        return True

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None