"""Custom adapter for user-defined API endpoints (e.g., Coding Plan).

OpenAI protocol is fully inherited from OpenAICompatAdapter (with retry
backoff wrapped around every request); only the Anthropic protocol branch
lives here.
"""
from typing import List, Optional
import asyncio
import httpx
import os

from .base import ChatResult
from .formatter import MessageFormatter
from .openai_compat import OpenAICompatAdapter

from src.utils import get_logger
from src.error import robust_json_parse, ErrorRecovery

logger = get_logger("adapters.custom")


class CustomAdapter(OpenAICompatAdapter):
    """Adapter for custom OpenAI-compatible or Anthropic-compatible endpoints.

    Configuration in config.yaml:
    ```yaml
    models:
      default: custom

      custom:
        base_url: https://api.codingplan.com/v1
        api_key: ${CUSTOM_API_KEY}
        model: gpt-4o
        api_protocol: openai   # "openai" or "anthropic"
    ```
    """

    PROVIDER_NAME = "custom"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            base_url=config.get("base_url", "https://api.openai.com/v1"),
            api_key=config.get("api_key"),
            model=config.get("model"),
            compat=config.get("compat"),
            api_protocol=config.get("api_protocol", "openai"),
            max_retries=config.get("max_retries", 3),
            retry_delay=config.get("retry_delay", 1.0),
        )

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = None,
        model: str = None,
        compat: dict = None,
        api_protocol: str = "openai",
        max_retries: int = 3,
        retry_delay: float = 1.0
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key or os.environ.get("CUSTOM_API_KEY"),
            model=model,
            compat=compat,
        )
        self.api_protocol = api_protocol.lower() if api_protocol else "openai"
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    # ── Shared request path (both protocols use /chat/completions retry) ──

    async def _retry_with_backoff(self, coro, operation_name: str = "API call"):
        """Retry an async operation with exponential backoff."""
        retryable = (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                return await coro()
            except retryable as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = ErrorRecovery.calculate_backoff_delay(attempt)
                    logger.warning(
                        f"{operation_name} failed (attempt {attempt + 1}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"{operation_name} failed after {self.max_retries} attempts")

        raise last_exception

    async def _post_chat(self, payload: dict) -> dict:
        """Wrap the shared chat-completions request with retry backoff."""
        if self.max_retries <= 1:
            return await super()._post_chat(payload)
        return await self._retry_with_backoff(
            lambda: super(CustomAdapter, self)._post_chat(payload),
            operation_name="OpenAI 兼容 API"
        )

    # ── Entry points: branch by protocol ──

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        if self.api_protocol == "anthropic":
            return await self._chat_anthropic(messages, system_prompt)
        return await super().chat(messages, system_prompt)

    async def chat_with_tools_and_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> ChatResult:
        if self.api_protocol != "anthropic":
            return await super().chat_with_tools_and_stop_reason(messages, tools, system_prompt)

        try:
            stop_reason, text, tool_calls = await self._try_anthropic_tool_call(
                messages, tools, system_prompt
            )

            # Parse-error retry via prompt injection
            for tc in tool_calls:
                if "__parse_error__" in tc.get("arguments", {}):
                    logger.warning("检测到工具参数解析错误，尝试 prompt injection 重试")
                    response, tool_calls = await self._chat_with_tool_prompt(
                        messages, tools, system_prompt
                    )
                    return ChatResult(text=response, tool_calls=tool_calls, stop_reason=stop_reason)

            return ChatResult(text=text, tool_calls=tool_calls, stop_reason=stop_reason)
        except Exception as e:
            logger.warning(f"工具调用失败: {e}，尝试 prompt injection")
            response, tool_calls = await self._chat_with_tool_prompt(messages, tools, system_prompt)
            return ChatResult(text=response, tool_calls=tool_calls, stop_reason=None)

    def get_name(self) -> str:
        return self.model or "custom"

    # ── Anthropic protocol branch ──

    def _get_anthropic_endpoint(self) -> str:
        """Return /messages if base_url already ends with /v1, else /v1/messages."""
        return "/messages" if self.base_url.endswith("/v1") else "/v1/messages"

    def _convert_tools_to_anthropic(self, tools: List[dict]) -> List[dict]:
        anthropic_tools = []
        for tool in tools:
            # OpenAI format: tool["function"]["parameters"] → Anthropic "input_schema"
            func = tool.get("function", tool)
            input_schema = func.get("parameters", tool.get("input_schema", {}))
            anthropic_tools.append({
                "name": func.get("name", tool.get("name")),
                "description": func.get("description", tool.get("description", "")),
                "input_schema": input_schema
            })
        return anthropic_tools

    def _extract_anthropic_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool_use blocks from an Anthropic response."""
        tool_calls = []
        for block in response.get("content", []):
            if block.get("type") != "tool_use":
                continue

            input_data = block.get("input", {})
            if "raw_arguments" in input_data:
                args = robust_json_parse(input_data["raw_arguments"])
                if "__parse_error__" in args:
                    logger.warning(f"[Custom] raw_arguments 解析失败: {args['__parse_error__']}")
            else:
                args = input_data

            tool_calls.append({
                "name": block.get("name"),
                "arguments": args,
                "id": block.get("id"),
            })
        return tool_calls

    async def _anthropic_request(self, payload: dict, operation_name: str) -> dict:
        """POST to the Anthropic endpoint with retry, raising friendly errors."""
        client = self._get_client()
        endpoint = self._get_anthropic_endpoint()

        response = await self._retry_with_backoff(
            lambda: client.post(endpoint, json=payload),
            operation_name=operation_name
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning("Anthropic API 错误 (400)，禁用工具支持")
                self._capabilities.supports_tools = False
                raise
            self._handle_http_error(e)
        return response.json()

    async def _chat_anthropic(self, messages: List[dict], system_prompt: str = None) -> str:
        if not self.model:
            raise ValueError(
                "Model name is required for custom adapter. "
                "Please specify 'model' in config.yaml"
            )

        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(messages, system_prompt)

        try:
            result = await self._anthropic_request(
                {
                    "model": self.model,
                    "max_tokens": 16384,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                },
                operation_name="Anthropic 聊天 API"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                return f"聊天请求失败 (400): {str(e)[:200]}"
            raise

        for block in result.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return ""

    async def _try_anthropic_tool_call(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[Optional[str], str, list[dict]]:
        """Tool calling via Anthropic protocol.

        @return Tuple of (stop_reason, response_text, tool_calls)
        """
        if not self.model:
            logger.error("Anthropic 工具调用缺少模型名称")
            raise ValueError("Model name is required")

        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(messages, system_prompt)
        anthropic_tools = self._convert_tools_to_anthropic(tools)

        try:
            result = await self._anthropic_request(
                {
                    "model": self.model,
                    "max_tokens": 16384,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                    "tools": anthropic_tools,
                },
                operation_name="Anthropic 工具调用 API"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                return None, f"工具调用失败 (400): {str(e)[:200]}", []
            raise

        stop_reason = result.get("stop_reason")
        tool_calls = self._extract_anthropic_tool_calls(result)

        text_content = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text_content = block.get("text", "")
                break

        logger.debug(f"Anthropic 工具调用成功 | tool_calls数={len(tool_calls)}")
        return stop_reason, text_content, tool_calls
