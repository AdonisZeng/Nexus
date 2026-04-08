"""Custom model adapter for user-defined API endpoints (e.g., Coding Plan)"""
from .base import ModelAdapter, StreamEvent, StreamEventType, ChatResult
from .formatter import MessageFormatter
from typing import Any, List, Optional, AsyncIterator
import asyncio
import httpx
import json
import re

from src.utils import get_logger

logger = get_logger("adapters.custom")


def _robust_json_parse(raw_str: str) -> dict:
    """Robustly parse a JSON string that may have escaping issues.

    Tries multiple strategies:
    1. Standard JSON parsing
    2. Fix unescaped newlines within JSON strings
    3. Extract key fields using regex

    @param raw_str: Raw string that should be JSON
    @return Parsed dictionary or error dict with __parse_error__
    """
    if not raw_str:
        return {"__parse_error__": "Empty raw_arguments"}

    # Strategy 1: Try standard JSON parsing
    try:
        return json.loads(raw_str)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Fix common escaping issues in JSON strings
    # The model often returns JSON where code content has literal newlines
    # instead of \n escapes, or has unescaped quotes within strings
    try:
        result = {}
        i = 0
        length = len(raw_str)

        # Simple state machine to find and fix JSON string values
        while i < length:
            # Find a string start
            if raw_str[i] == '"':
                # Mark start of string
                string_start = i
                i += 1
                string_content = []

                # Parse through the string, handling escapes
                while i < length:
                    char = raw_str[i]

                    if char == '\\':
                        # Escaped character - keep as-is
                        string_content.append(raw_str[i:i+2])
                        i += 2
                    elif char == '"':
                        # End of string
                        i += 1
                        break
                    elif char == '\n' or char == '\r':
                        # Unescaped newline in string - fix it
                        string_content.append('\\n')
                        i += 1
                    else:
                        string_content.append(char)
                        i += 1

                # Check what key this string belongs to
                # Look backwards for the key
                key_start = string_start
                while key_start > 0 and raw_str[key_start-1] in ' \t\n\r:':
                    key_start -= 1
                key_end = string_start
                while key_end < len(raw_str) and raw_str[key_end] != ':':
                    key_end += 1
                key = raw_str[key_start:key_end].strip() if (key_end > key_start) else ""

                # Look forward for colon
                value_start = i
                while value_start < length and raw_str[value_start] not in ':':
                    value_start += 1
                value_start += 1  # skip colon

                # Find the end of this value (comma, }, or end)
                value_end = value_start
                while value_end < length and raw_str[value_end] not in ',}':
                    value_end += 1

                # Store the fixed string content
                fixed_str = ''.join(string_content)
                if key in ('file_path', 'path', 'pattern', 'command'):
                    result[key] = fixed_str
                elif key == 'content' and 'content' not in result:
                    # For content, might appear multiple times - take first substantial one
                    result['content'] = fixed_str

            i += 1

        if result and len(result) >= 2:  # Require at least file_path and content
            logger.debug(f"[Custom] _robust_json_parse: 状态机解析成功: {list(result.keys())}")
            return result

    except Exception as e:
        logger.debug(f"[Custom] _robust_json_parse: 状态机解析失败: {e}")

    # Strategy 3: Try to extract fields using regex for common tool schemas
    try:
        result = {}

        # Try to extract file_path
        file_path_match = re.search(r'"file_path"\s*:\s*"([^"]*)"', raw_str)
        if file_path_match:
            result["file_path"] = file_path_match.group(1)

        # Try to extract content - more robust pattern that handles newlines
        content_match = re.search(r'"content"\s*:\s*"(.*?)"(?=\s*[,\}])', raw_str, re.DOTALL)
        if content_match:
            result["content"] = content_match.group(1)

        # Try to extract command
        cmd_match = re.search(r'"command"\s*:\s*"([^"]*)"', raw_str)
        if cmd_match:
            result["command"] = cmd_match.group(1)

        # Try to extract patch
        patch_match = re.search(r'"patch"\s*:\s*"(.*?)"(?=\s*[,\}])', raw_str, re.DOTALL)
        if patch_match:
            result["patch"] = patch_match.group(1)

        # Try to extract path
        path_match = re.search(r'"path"\s*:\s*"([^"]*)"', raw_str)
        if path_match:
            result["path"] = path_match.group(1)

        # Try to extract pattern
        pattern_match = re.search(r'"pattern"\s*:\s*"([^"]*)"', raw_str)
        if pattern_match:
            result["pattern"] = pattern_match.group(1)

        # Try to extract dir_path
        dir_path_match = re.search(r'"dir_path"\s*:\s*"([^"]*)"', raw_str)
        if dir_path_match:
            result["dir_path"] = dir_path_match.group(1)

        if result:
            logger.debug(f"[Custom] _robust_json_parse: 正则提取成功: {list(result.keys())}")
            return result

    except Exception as e:
        logger.debug(f"[Custom] _robust_json_parse: 正则提取失败: {e}")

    # All strategies failed
    logger.warning(f"[Custom] _robust_json_parse: 所有解析策略均失败")
    return {
        "__parse_error__": f"无法解析 JSON (长度={len(raw_str)}): {raw_str[:200]}...",
        "__raw_original__": raw_str[:2000]  # 保存原始内容供调试
    }


class CustomAdapter(ModelAdapter):
    """Adapter for custom OpenAI-compatible or Anthropic-compatible API endpoints.

    Use this for self-hosted models or API services like Coding Plan
    that provide OpenAI-compatible endpoints, or Anthropic-compatible
    endpoints (e.g., Claude API proxies).

    Configuration in config.yaml:
    ```yaml
    models:
      default: custom

      custom:
        base_url: https://api.codingplan.com/v1  # Your API endpoint
        api_key: ${CUSTOM_API_KEY}                # Your API key
        model: gpt-4o                              # Model name
        api_protocol: openai                       # "openai" or "anthropic"
        compat:
          supports_tools: true
          fallback_to_prompt_injection: false
    ```

    The adapter will:
    1. Try native tool calling first (based on api_protocol)
    2. Fall back to simple chat if tools not supported (or based on compat settings)
    3. Optionally use prompt injection fallback if enabled

    Supported API protocols:
    - "openai": OpenAI-compatible API (default)
    - "anthropic": Anthropic Messages API format
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
        """Initialize custom adapter.

        @param base_url API base URL
        @param api_key API key for authentication
        @param model Model name to use
        @param compat Compatibility settings
        @param api_protocol API protocol to use ("openai" or "anthropic")
        @param max_retries Maximum number of retries on API errors (default: 3)
        @param retry_delay Delay between retries in seconds (default: 1.0)
        """
        # Call parent constructor to initialize capabilities
        super().__init__(model=model, compat=compat)

        self.base_url = base_url.rstrip("/") if base_url else "https://api.openai.com/v1"
        self.api_key = api_key
        self.api_protocol = api_protocol.lower() if api_protocol else "openai"
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client = None

        logger.debug(
            f"CustomAdapter 初始化 | base_url={self.base_url} | "
            f"model={model} | api_protocol={self.api_protocol} | "
            f"compat={compat}"
        )

    def _get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=300.0,  # Configurable timeout
                headers={
                    "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                    "Content-Type": "application/json"
                }
            )
        return self._client

    async def _retry_with_backoff(
        self,
        coro,
        operation_name: str = "API call",
        retryable_exceptions: tuple = (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)
    ):
        """Retry an async operation with exponential backoff.

        @param coro: Coroutine to execute
        @param operation_name: Name for logging
        @param retryable_exceptions: Tuple of exceptions that trigger retry
        @return Result from successful execution
        @raises Last exception if all retries fail
        """
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                return await coro()
            except retryable_exceptions as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(
                        f"{operation_name} failed (attempt {attempt + 1}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"{operation_name} failed after {self.max_retries} attempts")

        raise last_exception

    def _build_messages(self, messages: List[dict], system_prompt: str = None) -> List[dict]:
        """Build messages in OpenAI-compatible format

        @param messages List of conversation messages
        @param system_prompt System prompt to prepend
        @return List of formatted messages
        """
        capabilities = self.get_capabilities()
        return MessageFormatter.to_openai(
            messages,
            system_prompt,
            supports_developer_role=capabilities.supports_developer_role
        )

    def _extract_tool_calls(self, message: dict) -> list[dict]:
        """Extract tool calls from response message.

        @param message Response message dict
        @return List of tool calls with name, arguments, and id
        """
        tool_calls = []
        for call in message.get("tool_calls", []):
            func = call.get("function", {})
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            tool_calls.append({
                "name": func.get("name"),
                "arguments": args,
                "id": call.get("id"),
            })
        return tool_calls

    def _extract_anthropic_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool calls from Anthropic response.

        Anthropic response format:
        {
            "content": [
                {"type": "text", "text": "..."},
                {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
            ]
        }

        @param response Anthropic API response dict
        @return List of tool calls with name, arguments, and id
        """
        tool_calls = []
        content_blocks = response.get("content", [])

        for block in content_blocks:
            if block.get("type") == "tool_use":
                name = block.get("name")
                input_data = block.get("input", {})

                if "raw_arguments" in input_data:
                    raw_args_str = input_data["raw_arguments"]
                    logger.debug(f"[Custom] raw_arguments 原始值长度={len(raw_args_str)} | 前100字符={raw_args_str[:100]}")
                    # Use robust parsing that handles escaping issues
                    args = _robust_json_parse(raw_args_str)
                    if "__parse_error__" in args:
                        logger.warning(f"[Custom] raw_arguments 解析失败: {args['__parse_error__']}")
                else:
                    args = input_data

                logger.debug(f"[Custom] 提取 Anthropic tool_use | name={name}")
                tool_calls.append({
                    "name": name,
                    "arguments": args,
                    "id": block.get("id"),
                })

        logger.debug(f"[Custom] 共提取 {len(tool_calls)} 个工具调用")
        return tool_calls

    def _get_anthropic_endpoint(self) -> str:
        """Get the correct endpoint for Anthropic API.

        If base_url ends with /v1, use /messages.
        Otherwise use /v1/messages.

        @return The API endpoint path
        """
        if self.base_url.endswith("/v1"):
            return "/messages"
        return "/v1/messages"

    def _convert_tools_to_anthropic(self, tools: List[dict]) -> List[dict]:
        """Convert tools to Anthropic format.

        @param tools List of tools in OpenAI format
        @return List of tools in Anthropic format
        """
        anthropic_tools = []
        for tool in tools:
            # OpenAI format: tool["function"]["parameters"]
            # Anthropic format: tool["input_schema"]
            func = tool.get("function", tool)
            input_schema = func.get("parameters", tool.get("input_schema", {}))
            
            anthropic_tools.append({
                "name": func.get("name", tool.get("name")),
                "description": func.get("description", tool.get("description", "")),
                "input_schema": input_schema
            })
        return anthropic_tools

    async def _try_anthropic_tool_call(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Try tool calling using Anthropic protocol.

        @param messages List of conversation messages
        @param tools List of available tools
        @param system_prompt System prompt to prepend
        @return Tuple of (response_text, tool_calls)
        """
        client = self._get_client()

        # Use MessageFormatter to convert messages
        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        # Convert tools to Anthropic format
        anthropic_tools = self._convert_tools_to_anthropic(tools)

        if not self.model:
            logger.error("Anthropic 工具调用缺少模型名称")
            raise ValueError("Model name is required")

        endpoint = self._get_anthropic_endpoint()

        async def _do_api_call():
            logger.debug(
                f"Anthropic 工具调用 | model={self.model} | "
                f"消息数={len(anthropic_messages)} | 工具数={len(anthropic_tools)}"
            )
            return await client.post(
                endpoint,
                json={
                    "model": self.model,
                    "max_tokens": 16384,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                    "tools": anthropic_tools,
                }
            )

        try:
            response = await self._retry_with_backoff(
                _do_api_call,
                operation_name="Anthropic 工具调用 API"
            )
            response.raise_for_status()
            result = response.json()

            # Debug: log raw API response for tool_use blocks
            for i, block in enumerate(result.get("content", [])):
                if block.get("type") == "tool_use":
                    input_data = block.get("input", {})
                    logger.debug(f"[Custom] API 返回 tool_use block #{i} | name={block.get('name')} | input keys={list(input_data.keys())}")
                    if "raw_arguments" in input_data:
                        logger.debug(f"[Custom] raw_arguments 存在，长度={len(input_data['raw_arguments'])}")

            # Extract tool calls from response
            tool_calls = self._extract_anthropic_tool_calls(result)

            # Extract text content
            text_content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    text_content = block.get("text", "")
                    break

            logger.debug(f"Anthropic 工具调用成功 | tool_calls数={len(tool_calls)}")
            return text_content, tool_calls

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning("Anthropic API 错误 (400)，禁用工具支持")
                self._capabilities.supports_tools = False
                # Return error to let caller handle fallback
                return f"工具调用失败 (400): {str(e)[:200]}", []
            if e.response.status_code == 401:
                logger.error("Anthropic API 认证失败 | 请检查 API key")
                raise ConnectionError(
                    "Authentication failed. Please check your API key"
                ) from None
            logger.error(f"Anthropic API HTTP 错误 | status={e.response.status_code} | {e}")
            raise
        except Exception as e:
            logger.error(f"Anthropic 工具调用异常 | {e}", exc_info=True)
            raise

    async def chat(self, messages: List[dict], system_prompt: str = None) -> str:
        """Send a chat message and get response.

        @param messages List of conversation messages
        @param system_prompt System prompt to prepend
        @return Response text from the model
        """
        logger.debug(
            f"发送聊天请求 | 消息数={len(messages)} | "
            f"system_prompt={bool(system_prompt)} | protocol={self.api_protocol}"
        )

        client = self._get_client()

        if not self.model:
            logger.error("缺少模型名称配置")
            raise ValueError(
                "Model name is required for custom adapter. "
                "Please specify 'model' in config.yaml"
            )

        # Use Anthropic protocol if specified
        if self.api_protocol == "anthropic":
            response = await self._chat_anthropic(client, messages, system_prompt)
            logger.debug(f"聊天响应接收 | 响应长度={len(response)}")
            return response

        # Default: Use OpenAI protocol
        response = await self._chat_openai(client, messages, system_prompt)
        logger.debug(f"聊天响应接收 | 响应长度={len(response)}")
        return response

    async def _chat_openai(
        self,
        client: httpx.AsyncClient,
        messages: List[dict],
        system_prompt: str = None
    ) -> str:
        """Send chat using OpenAI protocol.

        @param client HTTP client instance
        @param messages List of conversation messages
        @param system_prompt System prompt to prepend
        @return Response text from the model
        """
        # Build messages
        chat_messages = self._build_messages(messages, system_prompt)

        try:
            logger.debug(f"OpenAI API 请求 | model={self.model} | 消息数={len(chat_messages)}")
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

            # Validate response structure
            if not result.get("choices") or not result["choices"]:
                logger.error(f"OpenAI API 响应格式错误: 缺少 choices | response={str(result)[:200]}")
                raise ValueError("Invalid API response: missing choices")
            choice = result["choices"][0]
            if "message" not in choice:
                logger.error(f"OpenAI API 响应格式错误: 缺少 message | response={str(result)[:200]}")
                raise ValueError("Invalid API response: missing message")
            if "content" not in choice["message"]:
                logger.error(f"OpenAI API 响应格式错误: 缺少 content | response={str(result)[:200]}")
                raise ValueError("Invalid API response: missing content")

            return result["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("OpenAI API 认证失败 | 请检查 API key")
                raise ConnectionError(
                    "Authentication failed. Please check your API key in config.yaml"
                ) from None
            if e.response.status_code == 404:
                logger.error(f"OpenAI API 端点未找到 | base_url={self.base_url}")
                raise ConnectionError(
                    "API endpoint not found. Please check your base_url in config.yaml"
                ) from None
            logger.error(f"OpenAI API HTTP 错误 | status={e.response.status_code} | {e}")
            raise
        except Exception as e:
            logger.error(f"OpenAI API 请求异常 | {e}", exc_info=True)
            raise

    async def _chat_anthropic(
        self,
        client: httpx.AsyncClient,
        messages: List[dict],
        system_prompt: str = None
    ) -> str:
        """Send chat using Anthropic protocol.

        @param client HTTP client instance
        @param messages List of conversation messages
        @param system_prompt System prompt to prepend
        @return Response text from the model
        """
        # Use MessageFormatter to convert messages
        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        endpoint = self._get_anthropic_endpoint()

        async def _do_api_call():
            logger.debug(f"Anthropic API 请求 | model={self.model} | 消息数={len(anthropic_messages)}")
            return await client.post(
                endpoint,
                json={
                    "model": self.model,
                    "max_tokens": 16384,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                }
            )

        try:
            response = await self._retry_with_backoff(
                _do_api_call,
                operation_name="Anthropic 聊天 API"
            )
            response.raise_for_status()
            result = response.json()

            # Extract text content from response
            for block in result.get("content", []):
                if block.get("type") == "text":
                    return block.get("text", "")

            return ""
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("Anthropic API 认证失败 | 请检查 API key")
                raise ConnectionError(
                    "Authentication failed. Please check your API key in config.yaml"
                ) from None
            if e.response.status_code == 404:
                logger.error(f"Anthropic API 端点未找到 | base_url={self.base_url}")
                raise ConnectionError(
                    "API endpoint not found. Please check your base_url in config.yaml"
                ) from None
            logger.error(f"Anthropic API HTTP 错误 | status={e.response.status_code} | {e}")
            raise
        except Exception as e:
            logger.error(f"Anthropic API 请求异常 | {e}", exc_info=True)
            raise

    async def chat_with_tools(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Chat with tools support.

        @param messages List of conversation messages
        @param tools List of available tools
        @param system_prompt System prompt to prepend
        @return Tuple of (response_text, tool_calls)
        """
        logger.debug(
            f"工具调用请求 | 消息数={len(messages)} | 工具数={len(tools)} | "
            f"system_prompt={bool(system_prompt)}"
        )

        capabilities = self.get_capabilities()

        # Check if model supports tools
        if not capabilities.supports_tools:
            logger.debug("模型不支持工具调用，使用备选方案")
            if capabilities.fallback_to_prompt_injection:
                return await self._chat_with_tool_prompt(messages, tools, system_prompt)
            # Simple fallback to chat without tools
            response = await self.chat(messages, system_prompt)
            return response, []

        # Try native tool calling based on protocol
        try:
            if self.api_protocol == "anthropic":
                response_text, tool_calls = await self._try_anthropic_tool_call(
                    messages, tools, system_prompt
                )
            else:
                response_text, tool_calls = await self._try_native_tool_call(
                    messages, tools, system_prompt
                )
            logger.debug(f"工具调用完成 | tool_calls数={len(tool_calls)}")

            # Check for parse errors in tool calls - retry with prompt injection
            for tc in tool_calls:
                if "__parse_error__" in tc.get("arguments", {}):
                    logger.warning(f"检测到工具参数解析错误，尝试 prompt injection 重试")
                    return await self._chat_with_tool_prompt(messages, tools, system_prompt)

            return response_text, tool_calls
        except Exception as e:
            logger.warning(f"工具调用失败: {e}，尝试 prompt injection")
            return await self._chat_with_tool_prompt(messages, tools, system_prompt)

    async def chat_with_tools_and_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> "ChatResult":
        """Chat with tools, returning stop_reason if available.

        @return ChatResult with text, tool_calls, and stop_reason
        """
        from .base import ChatResult

        logger.debug(
            f"工具调用请求(含stop_reason) | 消息数={len(messages)} | 工具数={len(tools)} | "
            f"system_prompt={bool(system_prompt)}"
        )

        capabilities = self.get_capabilities()

        # Check if model supports tools
        if not capabilities.supports_tools:
            logger.debug("模型不支持工具调用，使用备选方案")
            if capabilities.fallback_to_prompt_injection:
                response, tool_calls = await self._chat_with_tool_prompt(messages, tools, system_prompt)
                return ChatResult(text=response, tool_calls=tool_calls, stop_reason=None)
            response = await self.chat(messages, system_prompt)
            return ChatResult(text=response, tool_calls=[], stop_reason=None)

        # Try native tool calling based on protocol
        try:
            stop_reason = None
            if self.api_protocol == "anthropic":
                stop_reason, response_text, tool_calls = await self._try_anthropic_tool_call_with_stop_reason(
                    messages, tools, system_prompt
                )
            else:
                stop_reason, response_text, tool_calls = await self._try_native_tool_call_with_stop_reason(
                    messages, tools, system_prompt
                )
            logger.debug(f"工具调用完成 | tool_calls数={len(tool_calls)}")

            # Check for parse errors in tool calls - retry with prompt injection
            for tc in tool_calls:
                if "__parse_error__" in tc.get("arguments", {}):
                    logger.warning(f"检测到工具参数解析错误，尝试 prompt injection 重试")
                    response, tool_calls = await self._chat_with_tool_prompt(messages, tools, system_prompt)
                    return ChatResult(text=response, tool_calls=tool_calls, stop_reason=stop_reason)

            return ChatResult(text=response_text, tool_calls=tool_calls, stop_reason=stop_reason)
        except Exception as e:
            logger.warning(f"工具调用失败: {e}，尝试 prompt injection")
            response, tool_calls = await self._chat_with_tool_prompt(messages, tools, system_prompt)
            return ChatResult(text=response, tool_calls=tool_calls, stop_reason=None)

    async def _try_native_tool_call_with_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[Optional[str], str, list[dict]]:
        """Try native tool calling using OpenAI protocol, returning stop_reason.

        @return Tuple of (stop_reason, response_text, tool_calls)
        """
        client = self._get_client()

        chat_messages = self._build_messages(messages, system_prompt)

        if not self.model:
            logger.error("OpenAI 工具调用缺少模型名称")
            raise ValueError("Model name is required")

        try:
            logger.debug(
                f"OpenAI 工具调用 | model={self.model} | "
                f"消息数={len(chat_messages)} | 工具数={len(tools)}"
            )
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

            # Extract finish_reason (OpenAI's stop_reason)
            finish_reason = result.get("choices", [{}])[0].get("finish_reason")

            message = result.get("choices", [{}])[0].get("message", {})
            tool_calls = self._extract_tool_calls(message)

            logger.debug(f"OpenAI 工具调用成功 | tool_calls数={len(tool_calls)}")
            return finish_reason, message.get("content") or "", tool_calls

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning("模型不支持工具调用 (400)")
                self._capabilities.supports_tools = False
                return None, f"工具调用失败 (400): {str(e)[:200]}", []
            if e.response.status_code == 401:
                logger.error("OpenAI API 认证失败")
                raise ConnectionError("Authentication failed. Please check your API key") from None
            logger.error(f"OpenAI API HTTP 错误 | status={e.response.status_code} | {e}")
            raise
        except Exception as e:
            logger.error(f"OpenAI 工具调用异常 | {e}", exc_info=True)
            raise

    async def _try_anthropic_tool_call_with_stop_reason(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[Optional[str], str, list[dict]]:
        """Try tool calling using Anthropic protocol, returning stop_reason.

        @return Tuple of (stop_reason, response_text, tool_calls)
        """
        client = self._get_client()

        system_prompt, anthropic_messages = MessageFormatter.to_anthropic(
            messages, system_prompt
        )

        anthropic_tools = self._convert_tools_to_anthropic(tools)

        if not self.model:
            logger.error("Anthropic 工具调用缺少模型名称")
            raise ValueError("Model name is required")

        endpoint = self._get_anthropic_endpoint()

        try:
            logger.debug(
                f"Anthropic 工具调用 | model={self.model} | "
                f"消息数={len(anthropic_messages)} | 工具数={len(anthropic_tools)}"
            )
            response = await client.post(
                endpoint,
                json={
                    "model": self.model,
                    "max_tokens": 16384,
                    "system": system_prompt,
                    "messages": anthropic_messages,
                    "tools": anthropic_tools,
                }
            )
            response.raise_for_status()
            result = response.json()

            # Extract stop_reason from Anthropic response
            stop_reason = result.get("stop_reason")

            # Debug: log raw API response for tool_use blocks
            for i, block in enumerate(result.get("content", [])):
                if block.get("type") == "tool_use":
                    input_data = block.get("input", {})
                    logger.debug(f"[Custom] API 返回 tool_use block #{i} | name={block.get('name')} | input keys={list(input_data.keys())}")

            # Extract tool calls from response
            tool_calls = self._extract_anthropic_tool_calls(result)

            # Extract text content
            text_content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    text_content = block.get("text", "")
                    break

            logger.debug(f"Anthropic 工具调用成功 | tool_calls数={len(tool_calls)}")
            return stop_reason, text_content, tool_calls

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.warning("Anthropic API 错误 (400)，禁用工具支持")
                self._capabilities.supports_tools = False
                return None, f"工具调用失败 (400): {str(e)[:200]}", []
            if e.response.status_code == 401:
                logger.error("Anthropic API 认证失败")
                raise ConnectionError("Authentication failed. Please check your API key") from None
            logger.error(f"Anthropic API HTTP 错误 | status={e.response.status_code} | {e}")
            raise
        except Exception as e:
            logger.error(f"Anthropic 工具调用异常 | {e}", exc_info=True)
            raise

    async def _try_native_tool_call(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Try native tool calling using OpenAI protocol.

        @param messages List of conversation messages
        @param tools List of available tools
        @param system_prompt System prompt to prepend
        @return Tuple of (response_text, tool_calls)
        """
        client = self._get_client()

        chat_messages = self._build_messages(messages, system_prompt)

        if not self.model:
            logger.error("OpenAI 工具调用缺少模型名称")
            raise ValueError("Model name is required")

        async def _do_api_call():
            logger.debug(
                f"OpenAI 工具调用 | model={self.model} | "
                f"消息数={len(chat_messages)} | 工具数={len(tools)}"
            )
            return await client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": chat_messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "stream": False,
                }
            )

        try:
            response = await self._retry_with_backoff(
                _do_api_call,
                operation_name="OpenAI 工具调用 API"
            )
            response.raise_for_status()
            result = response.json()

            message = result["choices"][0]["message"]
            tool_calls = self._extract_tool_calls(message)

            logger.debug(f"OpenAI 工具调用成功 | tool_calls数={len(tool_calls)}")
            return message.get("content") or "", tool_calls

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                # Model doesn't support tools - disable and return error for fallback
                logger.warning("模型不支持工具调用 (400)，禁用工具支持")
                self._capabilities.supports_tools = False
                # Return error to let caller handle fallback
                return f"工具调用失败 (400): {str(e)[:200]}", []
            if e.response.status_code == 401:
                logger.error("OpenAI API 认证失败 | 请检查 API key")
                raise ConnectionError(
                    "Authentication failed. Please check your API key"
                ) from None
            logger.error(f"OpenAI API HTTP 错误 | status={e.response.status_code} | {e}")
            raise
        except Exception as e:
            logger.error(f"OpenAI 工具调用异常 | {e}", exc_info=True)
            raise

    async def _chat_with_tool_prompt(
        self,
        messages: List[dict],
        tools: List[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Fallback: use prompt injection for tool calling.

        @param messages List of conversation messages
        @param tools List of available tools
        @param system_prompt System prompt to prepend
        @return Tuple of (response_text, tool_calls)
        """
        tool_prompt = self._build_tool_prompt(tools)
        enhanced_prompt = f"{system_prompt}\n\n{tool_prompt}" if system_prompt else tool_prompt

        response = await self.chat(messages, enhanced_prompt)
        tool_calls = self._parse_tool_calls_from_response(response)

        return response, tool_calls

    def _build_tool_prompt(self, tools: List[dict]) -> str:
        """Build tool prompt for prompt injection.

        @param tools List of available tools
        @return Formatted tool prompt string
        """
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
        """Parse tool calls from model response.

        @param response Model response text
        @return List of parsed tool calls
        """
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

    async def chat_stream(
        self,
        messages: List[dict],
        tools: List[dict] = None,
        system_prompt: str = None
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat with tool support.

        This override uses chat_with_tools_and_stop_reason to get stop_reason
        and emits it in the MESSAGE_STOP event.
        """
        # Use chat_with_tools_and_stop_reason to get stop_reason
        result = await self.chat_with_tools_and_stop_reason(
            messages, tools, system_prompt
        )

        # Emit text delta
        if result.text:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, content=result.text)

        # Emit tool calls if any
        if result.tool_calls:
            yield StreamEvent(
                type=StreamEventType.TOOL_USE_COMPLETE,
                tool_calls=result.tool_calls
            )

        # Emit MESSAGE_STOP with stop_reason
        yield StreamEvent(
            type=StreamEventType.MESSAGE_STOP,
            stop_reason=result.stop_reason
        )

    def get_name(self) -> str:
        return self.model or "custom"

    def supports_streaming(self) -> bool:
        return True

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None