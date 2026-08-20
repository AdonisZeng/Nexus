"""Ollama local model adapter"""
from typing import List
import httpx
import logging

from .base import ModelAdapter
from .errors import handle_http_errors, check_tool_call_parse_errors_and_retry
from .formatter import MessageFormatter

logger = logging.getLogger("Nexus")


class OllamaAdapter(ModelAdapter):
    """Adapter for Ollama local models

    Note: Most Ollama models don't support native tool calling.
    This adapter will try native tool calling first, then fall back
    to prompt injection if enabled.
    """

    PROVIDER_NAME = "ollama"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            base_url=config.get("url", "http://localhost:11434"),
            model=config.get("model", "llama3"),
            compat=config.get("compat"),
        )

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        compat: dict = None
    ):
        super().__init__(model=model, compat=compat)

        self.base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        return self._client

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        client = self._get_client()

        response = await client.post(
            "/api/chat",
            json={
                "model": self.model,
                "messages": MessageFormatter.to_ollama(messages, system_prompt),
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

        if not capabilities.supports_tools:
            if capabilities.fallback_to_prompt_injection:
                return await self._chat_with_tool_prompt(messages, tools, system_prompt)
            response = await self.chat(messages, system_prompt)
            return response, []

        # Try native tool calling (Ollama 0.1.20+ supports tools)
        try:
            response_text, tool_calls = await self._try_native_tool_call(messages, tools, system_prompt)

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

        # Ollama uses a different format for tools
        ollama_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {})
                }
            }
            for tool in tools
        ]

        try:
            response = await client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": MessageFormatter.to_ollama(messages, system_prompt),
                    "tools": ollama_tools,
                    "stream": False
                }
            )
            response.raise_for_status()
            result = response.json()

            message = result.get("message", {})
            tool_calls = self._extract_openai_tool_calls(message)

            return message.get("content") or "", tool_calls

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning("Model doesn't support tool calling (400), disabling")
                self._capabilities.supports_tools = False
                raise
            handle_http_errors(e, "Ollama")
            raise

    def get_name(self) -> str:
        return "ollama"

    def supports_streaming(self) -> bool:
        return True
