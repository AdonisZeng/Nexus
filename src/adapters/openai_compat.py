"""Unified OpenAI-compatible adapter (single httpx implementation).

Replaces the previous per-provider duplicates (openai SDK, xai, lmstudio):
one chat-completions implementation with subclass hooks for provider quirks.
"""
from typing import List, Optional

import httpx

from .base import ModelAdapter, ChatResult
from .formatter import MessageFormatter
from .errors import (
    validate_openai_response,
    handle_http_errors,
    check_tool_call_parse_errors_and_retry,
    decode_html_entities,
)

from src.utils import get_logger

logger = get_logger("adapters.openai_compat")


class OpenAICompatAdapter(ModelAdapter):
    """Adapter for any OpenAI chat-completions compatible endpoint.

    Subclass hooks:
    - `_ensure_model()`: resolve model name before a request (auto-detect)
    - `_extra_payload()`: extra request parameters (e.g. temperature)
    - `_extract_result()`: (text, tool_calls) extraction from response message
    - `_handle_http_error()`: provider-specific HTTP error translation
    """

    DEFAULT_BASE_URL = "http://localhost:8000/v1"
    DEFAULT_TIMEOUT = 300.0

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        model: str = None,
        compat: dict = None,
        timeout: float = None
    ):
        super().__init__(model=model, compat=compat)

        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, headers=headers
            )
        return self._client

    async def _ensure_model(self) -> str:
        """Resolve the model name before a request. Subclasses may auto-detect."""
        if not self.model:
            raise ValueError(
                f"Model name is required for {self.get_name()}. "
                "Please specify 'model' in config.yaml"
            )
        return self.model

    def _extra_payload(self) -> dict:
        """Extra request parameters merged into every payload."""
        return {}

    def _build_messages(self, messages: List[dict], system_prompt: str = None) -> List[dict]:
        capabilities = self.get_capabilities()
        return MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

    async def _post_chat(self, payload: dict) -> dict:
        """POST /chat/completions and return the parsed response body."""
        client = self._get_client()
        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            logger.error(f"[{self.get_name()}] 请求超时（{self.timeout:.0f}秒），模型可能卡住了")
            raise TimeoutError("Model request timeout - the model may be stuck") from None
        except httpx.HTTPStatusError as e:
            self._handle_http_error(e)

    def _handle_http_error(self, e: httpx.HTTPStatusError) -> None:
        if e.response.status_code == 502:
            raise ConnectionError(
                "Model server is not responding. "
                "Please make sure the local server is started."
            ) from None
        handle_http_errors(e, self.get_name(), "API key")

    def _extract_result(self, message: dict) -> tuple[str, list[dict]]:
        """Extract (text, tool_calls) from an OpenAI-format response message."""
        if self.get_capabilities().tool_call_arguments_encoding == "html-entities":
            for call in message.get("tool_calls") or []:
                func = call.get("function") or {}
                if isinstance(func.get("arguments"), str):
                    func["arguments"] = decode_html_entities(func["arguments"])
        return message.get("content") or "", self._extract_openai_tool_calls(message)

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        model = await self._ensure_model()
        payload = {
            "model": model,
            "messages": self._build_messages(messages, system_prompt),
            "stream": False,
            **self._extra_payload(),
        }
        result = await self._post_chat(payload)
        validate_openai_response(result, context=self.get_name())
        return result["choices"][0]["message"]["content"]

    async def chat_with_tools_and_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> ChatResult:
        model = await self._ensure_model()
        payload = {
            "model": model,
            "messages": self._build_messages(messages, system_prompt),
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
            **self._extra_payload(),
        }
        result = await self._post_chat(payload)
        validate_openai_response(result, context=self.get_name())

        finish_reason = result.get("choices", [{}])[0].get("finish_reason")
        message = result["choices"][0]["message"]
        text, tool_calls = self._extract_result(message)

        # Check for parse errors and retry via prompt injection if needed
        retry_result = await check_tool_call_parse_errors_and_retry(
            tool_calls,
            lambda: self._chat_with_tool_prompt(messages, tools, system_prompt)
        )
        if retry_result:
            return ChatResult(
                text=retry_result[0], tool_calls=retry_result[1], stop_reason=finish_reason
            )

        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=finish_reason)

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        capabilities = self.get_capabilities()

        if not capabilities.supports_tools:
            if capabilities.fallback_to_prompt_injection:
                return await self._chat_with_tool_prompt(messages, tools, system_prompt)
            response = await self.chat(messages, system_prompt)
            return response, []

        try:
            result = await self.chat_with_tools_and_stop_reason(messages, tools, system_prompt)
            return result.text, result.tool_calls
        except TimeoutError:
            raise
        except Exception as e:
            logger.warning(f"Tool calling failed: {e}, trying prompt injection")
            return await self._chat_with_tool_prompt(messages, tools, system_prompt)

    def get_name(self) -> str:
        return self.model or self.__class__.__name__

    def supports_streaming(self) -> bool:
        return True
