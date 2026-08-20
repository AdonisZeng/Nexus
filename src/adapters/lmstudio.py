"""LMStudio local model adapter (OpenAI-compatible httpx implementation).

Adds model auto-detection, brace-balanced XML tool-call parsing and
timeout tolerance on top of the shared OpenAI-compatible implementation.
"""
import json
import re
from typing import List

from .openai_compat import OpenAICompatAdapter

from src.utils import get_logger

logger = get_logger("adapters.lmstudio")


class LMStudioAdapter(OpenAICompatAdapter):
    """Adapter for LMStudio local models at http://localhost:1234/v1"""

    PROVIDER_NAME = "lmstudio"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            base_url=config.get("url", "http://localhost:1234/v1"),
            model=config.get("model"),
            compat=config.get("compat"),
        )

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = None,
        compat: dict = None
    ):
        super().__init__(base_url=base_url, model=model, compat=compat)
        self._available_models = None

    async def _get_available_models(self) -> List[str]:
        """Get list of available models from LMStudio"""
        if self._available_models is not None:
            return self._available_models

        try:
            client = self._get_client()
            response = await client.get("/models")
            if response.status_code == 200:
                data = response.json()
                self._available_models = [m["id"] for m in data.get("data", [])]
            else:
                self._available_models = []
        except Exception:
            self._available_models = []

        return self._available_models

    async def _ensure_model(self) -> str:
        """Auto-detect a loaded model when none is configured."""
        if self.model:
            return self.model

        models = await self._get_available_models()
        if not models:
            raise ConnectionError(
                "LMStudio is not running or no model is loaded.\n"
                "Please:\n"
                "1. Open LMStudio\n"
                "2. Load a model\n"
                "3. Start the local server (default: http://localhost:1234)"
            )
        self.model = models[0]
        return self.model

    def _extra_payload(self) -> dict:
        return {"temperature": 0.7}

    def _extract_result(self, message: dict) -> tuple[str, list[dict]]:
        """Also parse XML tool calls embedded in content (common on local models)."""
        text, tool_calls = super()._extract_result(message)
        if not tool_calls and text and "<tool_call" in text:
            logger.info("[LMStudio] 无原生 tool_calls，从 content 解析 XML 工具调用")
            return "", self._parse_tool_calls_from_response(text)
        return text, tool_calls

    def _parse_tool_calls_from_response(self, response: str) -> List[dict]:
        """Brace-balanced parser tolerant of missing closing tags."""
        tool_calls = []

        tool_call_pattern = r'<tool_call(?:\s+name="([^"]+)")?[^>]*>'
        for tc_match in re.finditer(tool_call_pattern, response):
            name = tc_match.group(1)
            start_pos = tc_match.end()

            if name is None:
                func_match = re.search(
                    r'<function=([^>]+)>', response[tc_match.start():tc_match.start() + 100]
                )
                if func_match:
                    name = func_match.group(1).strip()

            if not name:
                logger.warning("[LMStudio] 无法解析 tool_call 名称")
                continue

            json_start = response.find('{', start_pos)
            if json_start == -1:
                logger.warning(f"[LMStudio] tool_call '{name}' 后未找到 JSON 开始")
                continue

            brace_count = 0
            json_end = json_start
            for i in range(json_start, len(response)):
                if response[i] == '{':
                    brace_count += 1
                elif response[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break

            args_str = response[json_start:json_end]
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {"raw": args_str}
            tool_calls.append({
                "name": name,
                "arguments": args,
                "id": f"prompt_{len(tool_calls)}",
            })

        if not tool_calls:
            logger.warning("[LMStudio] 未解析到任何工具调用")

        return tool_calls

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Like the shared implementation, but a prompt-injection timeout
        yields an empty result instead of propagating."""
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
            logger.error("[LMStudio] 原生工具调用超时")
            raise
        except Exception as e:
            logger.warning(f"Tool calling failed: {e}, trying prompt injection")
            try:
                return await self._chat_with_tool_prompt(messages, tools, system_prompt)
            except TimeoutError:
                logger.error("[LMStudio] Prompt injection 请求超时，跳过此轮")
                return "", []

    def get_name(self) -> str:
        return "lmstudio"
