"""LMStudio local model adapter"""
from .base import ModelAdapter
from .formatter import MessageFormatter
from .errors import (
    robust_json_parse,
    validate_openai_response,
    handle_http_errors,
    check_tool_call_parse_errors_and_retry,
)
from typing import Any, List, Optional
import httpx
import json
import logging

logger = logging.getLogger("Nexus")


class LMStudioAdapter(ModelAdapter):
    """Adapter for LMStudio local models

    LMStudio provides an OpenAI-compatible API at http://localhost:1234/v1
    """

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
        # Call parent constructor to initialize capabilities
        super().__init__(model=model, compat=compat)

        self.base_url = base_url.rstrip("/")
        self._client = None
        self._available_models = None

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=300.0  # Long timeout for local models
            )
        return self._client

    async def _get_available_models(self) -> List[str]:
        """Get list of available models from LMStudio"""
        if self._available_models is not None:
            return self._available_models

        try:
            client = self._get_client()
            response = await client.get("/models")
            if response.status_code == 200:
                data = response.json()
                self._available_models = [
                    m["id"] for m in data.get("data", [])
                ]
            else:
                self._available_models = []
        except Exception:
            self._available_models = []

        return self._available_models

    async def _ensure_model(self) -> str:
        """Ensure we have a model name"""
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

    def _build_messages(self, messages: List[dict], system_prompt: str = None) -> List[dict]:
        """Build messages in LMStudio format"""
        return MessageFormatter.to_lmstudio(messages, system_prompt)

    def _extract_tool_calls(self, message: dict) -> list[dict]:
        """Extract tool calls from response message"""
        tool_calls = []
        for call in message.get("tool_calls", []):
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
                "id": call.get("id"),
            })
        return tool_calls

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        client = self._get_client()

        chat_messages = self._build_messages(messages, system_prompt)
        model = await self._ensure_model()

        try:
            response = await client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": chat_messages,
                    "stream": False,
                    "temperature": 0.7,
                }
            )
            response.raise_for_status()
            result = response.json()
            validate_openai_response(result, context="LMStudio")
            return result["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            logger.error("[LMStudio] 请求超时（300秒），模型可能卡住了")
            raise TimeoutError("Model request timeout - the model may be stuck") from None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 502:
                raise ConnectionError(
                    "LMStudio server is not responding.\n"
                    "Please make sure:\n"
                    "1. LMStudio is running\n"
                    "2. A model is loaded\n"
                    "3. Local server is started (click 'Start Server' in LMStudio)"
                ) from None
            handle_http_errors(e, "LMStudio")
            raise

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

    async def _try_native_tool_call(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Try native tool calling"""
        logger.info("[LMStudio] [_try_native_tool_call] 开始原生工具调用")
        client = self._get_client()
        model = await self._ensure_model()

        chat_messages = self._build_messages(messages, system_prompt)

        try:
            response = await client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": chat_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                }
            )
            response.raise_for_status()
            result = response.json()

            message = result["choices"][0]["message"]
            content = message.get("content") or ""
            tool_calls_list = message.get("tool_calls", [])

            logger.info(f"[LMStudio] 原生调用返回 | has_content={bool(content)} | has_tool_calls={bool(tool_calls_list)} | tool_calls数量={len(tool_calls_list)}")

            if tool_calls_list:
                for tc in tool_calls_list:
                    logger.info(f"[LMStudio] 原生 tool_call: {tc.get('function', {}).get('name')}")
                tool_calls = self._extract_tool_calls(message)
                return content, tool_calls

            if content and "<tool_call" in content:
                logger.info("[LMStudio] 原生调用成功但无 tool_calls，content 中包含 <tool_call>，使用 prompt injection 解析")
                tool_calls = self._parse_tool_calls_from_response(content)
                logger.info(f"[LMStudio] 从 content 中解析出 {len(tool_calls)} 个工具调用")
                return "", tool_calls

            return content, []

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning(f"Model doesn't support tool calling (400)")
                raise

            raise
        except Exception as e:
            logger.error(f"Tool calling error: {e}")
            raise

    async def _chat_with_tool_prompt(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Fallback: use prompt injection for tool calling"""
        logger.info("[LMStudio] 触发 prompt injection fallback")
        tool_prompt = self._build_tool_prompt(tools)
        logger.debug(f"[LMStudio] 构建的 tool_prompt:\n{tool_prompt[:500]}...")
        enhanced_prompt = f"{system_prompt}\n\n{tool_prompt}" if system_prompt else tool_prompt

        logger.info("[LMStudio] 发送 prompt injection 请求")
        response = await self.chat(messages, enhanced_prompt)
        logger.info(f"[LMStudio] prompt injection 返回 response 长度: {len(response)}")
        logger.debug(f"[LMStudio] prompt injection 返回内容:\n{response[:1000]}...")

        tool_calls = self._parse_tool_calls_from_response(response)
        logger.info(f"[LMStudio] 解析出 {len(tool_calls)} 个工具调用")

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
        logger.debug(f"[LMStudio] 开始解析工具调用，response长度={len(response)}")

        tool_call_pattern = r'<tool_call(?:\s+name="([^"]+)")?[^>]*>'
        tool_calls_matches = list(re.finditer(tool_call_pattern, response))

        logger.info(f"[LMStudio] 找到 {len(tool_calls_matches)} 个 <tool_call> 标签")

        for tc_match in tool_calls_matches:
            name = tc_match.group(1)
            start_pos = tc_match.end()

            if name is None:
                func_match = re.search(r'<function=([^>]+)>', response[tc_match.start():tc_match.start()+100])
                if func_match:
                    name = func_match.group(1).strip()

            if not name:
                logger.warning(f"[LMStudio] 无法解析 tool_call 名称")
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
            logger.debug(f"[LMStudio] 解析到工具: {name}, JSON: {args_str[:150]}...")

            try:
                args = json.loads(args_str)
                tool_calls.append({
                    "name": name,
                    "arguments": args,
                    "id": f"prompt_{len(tool_calls)}",
                })
                logger.info(f"[LMStudio] 成功解析工具: {name}")
            except json.JSONDecodeError as e:
                logger.warning(f"[LMStudio] JSON 解析失败: {e}, raw: {args_str[:150]}")
                tool_calls.append({
                    "name": name,
                    "arguments": {"raw": args_str},
                    "id": f"prompt_{len(tool_calls)}",
                })

        if not tool_calls:
            logger.warning("[LMStudio] 未解析到任何工具调用，检查 response 内容:")
            logger.warning(f"  response 包含 '<tool_call': {'<tool_call' in response}")
            for i, line in enumerate(response.split('\n')[:10]):
                logger.warning(f"  [{i}] {line[:100]}")

        return tool_calls

    def get_name(self) -> str:
        return "lmstudio"

    def supports_streaming(self) -> bool:
        return True

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None