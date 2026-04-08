"""xAI Grok model adapter."""
from .base import ModelAdapter
from .formatter import MessageFormatter
from .errors import (
    try_repair_malformed_json,
    validate_openai_response,
    handle_http_errors,
    check_tool_call_parse_errors_and_retry,
)
from typing import List
import httpx
import json

from src.utils import get_logger

logger = get_logger("adapters.xai")


class XAIAdapter(ModelAdapter):
    """Adapter for xAI Grok models.

    xAI Grok models use HTML entity encoding for tool call arguments
    (e.g., &quot; instead of "). This adapter handles the decoding.

    Configuration in config.yaml:
    ```yaml
    models:
      default: xai

      xai:
        base_url: https://api.x.ai/v1
        api_key: ${XAI_API_KEY}
        model: grok-2
    ```

    Note: xAI uses OpenAI-compatible API format but requires special
    handling of tool call arguments that may be HTML-encoded.
    """

    PROVIDER_NAME = "xai"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            api_key=config.get("api_key"),
            base_url=config.get("base_url", "https://api.x.ai/v1"),
            model=config.get("model", "grok-2"),
            compat=config.get("compat"),
        )

    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-2",
        compat: dict = None
    ):
        """Initialize xAI adapter.

        @param api_key: xAI API key
        @param base_url: API base URL
        @param model: Model name
        @param compat: Compatibility settings
        """
        super().__init__(model=model, compat=compat)

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = None

        # Enable HTML entity decoding for xAI/Grok models
        capabilities = self.get_capabilities()
        if capabilities.tool_call_arguments_encoding is None:
            capabilities.tool_call_arguments_encoding = "html-entities"

        logger.debug(
            f"XAIAdapter 初始化 | base_url={self.base_url} | "
            f"model={model} | encoding={capabilities.tool_call_arguments_encoding}"
        )

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=300.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
            )
        return self._client

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        """Send a chat message and get response.

        @param messages: List of conversation messages
        @param system_prompt: System prompt to prepend
        @return: Response text from the model
        """
        client = self._get_client()

        capabilities = self.get_capabilities()
        chat_messages = MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

        try:
            response = await client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": chat_messages,
                    "stream": False,
                }
            )
            response.raise_for_status()
            result = response.json()
            validate_openai_response(result, context="xAI")
            return result["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            handle_http_errors(e, "xAI", "API key")
            raise

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Chat with tools support.

        @param messages: List of conversation messages
        @param tools: List of available tools
        @param system_prompt: System prompt to prepend
        @return: Tuple of (response_text, tool_calls)
        """
        client = self._get_client()

        capabilities = self.get_capabilities()
        chat_messages = MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

        try:
            response = await client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": chat_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                }
            )
            response.raise_for_status()
            result = response.json()
            validate_openai_response(result, context="xAI")

            message = result["choices"][0]["message"]
            tool_calls = []

            if message.get("tool_calls"):
                for call in message["tool_calls"]:
                    func = call.get("function", {})
                    args = func.get("arguments", {})

                    # Handle string arguments with HTML entity decoding
                    if isinstance(args, str):
                        # Try repair with HTML entity decoding
                        parsed = try_repair_malformed_json(args)
                        if parsed is not None:
                            args = parsed
                        else:
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"raw": args}

                    tool_calls.append({
                        "name": func.get("name"),
                        "arguments": args,
                        "id": call.get("id"),
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
            handle_http_errors(e, "xAI", "API key")
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
        return self.model or "grok-2"

    def supports_streaming(self) -> bool:
        return True

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
