"""Model adapter base class"""
import json
import re
from abc import ABC, abstractmethod
from typing import Optional, AsyncIterator
from dataclasses import dataclass
from enum import Enum, auto

from .capabilities import ModelCapabilities, merge_capabilities, infer_capabilities_from_model_name

from src.utils import get_logger

logger = get_logger("adapters.base")

# XML closing tag built via concatenation to keep this file tooling-safe
_TOOL_CALL_CLOSE = "</" + "tool_call>"
_TOOL_CALL_PATTERN = re.compile(
    r'<tool_call\s+name="([^"]+)">\s*([\s\S]*?)\s*' + re.escape(_TOOL_CALL_CLOSE)
)


class StreamEventType(Enum):
    """流式事件类型"""
    TEXT_DELTA = auto()       # 文本增量
    TOOL_USE_START = auto()   # 工具调用开始
    TOOL_USE_DELTA = auto()   # 工具调用参数增量
    TOOL_USE_COMPLETE = auto() # 工具调用完成
    MESSAGE_STOP = auto()     # 消息结束
    ERROR = auto()            # 错误


@dataclass
class StreamEvent:
    """流式事件"""
    type: StreamEventType
    content: Optional[str] = None
    tool_name: Optional[str] = None
    tool_id: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_calls: Optional[list] = None
    error: Optional[str] = None
    stop_reason: Optional[str] = None


@dataclass
class ChatResult:
    """Chat result with optional stop reason

    Attributes:
        text: Response text from the model
        tool_calls: List of tool calls made by the model
        stop_reason: Why the model stopped (stop, length, tool_calls, etc.)
    """
    text: str
    tool_calls: list[dict]
    stop_reason: Optional[str] = None  # "stop", "length", "tool_calls", etc.


class ModelAdapter(ABC):
    """Base class for model adapters"""

    # Provider name - set in subclass to auto-register
    PROVIDER_NAME: str = None

    def __init_subclass__(cls, **kwargs):
        """Auto-register adapter when subclassed.

        If subclass has PROVIDER_NAME set, it auto-registers to AdapterRegistry.
        """
        super().__init_subclass__(**kwargs)
        if hasattr(cls, 'PROVIDER_NAME') and cls.PROVIDER_NAME:
            from .registry import AdapterRegistry
            AdapterRegistry.register(cls.PROVIDER_NAME, cls)

    def __init__(
        self,
        model: str = None,
        capabilities: ModelCapabilities = None,
        compat: dict = None
    ):
        """Initialize model adapter.

        @param model Model name
        @param capabilities Model capabilities
        @param compat Compatibility settings
        """
        self.model = model

        # Merge explicit capabilities with inferred ones
        inferred = infer_capabilities_from_model_name(model) if model else {}
        self._capabilities = merge_capabilities(explicit=compat, inferred=inferred)

        logger.debug(
            f"ModelAdapter 初始化 | model={model} | "
            f"capabilities={self._capabilities}"
        )

    def get_capabilities(self) -> ModelCapabilities:
        """Get model capabilities

        @return ModelCapabilities instance
        """
        return self._capabilities

    @abstractmethod
    async def chat(self, messages: list[dict], system_prompt: str = None) -> str:
        """Simple chat without tools

        @param messages List of conversation messages
        @param system_prompt System prompt to prepend
        @return Response text from the model
        """
        pass

    @abstractmethod
    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Chat with tool support.

        @param messages List of conversation messages
        @param tools List of available tools
        @param system_prompt System prompt to prepend
        @return Tuple of (response_text, tool_calls) where tool_calls is a list of
                {"name": str, "arguments": dict}
        """
        pass

    async def chat_with_tools_and_stop_reason(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = None
    ) -> ChatResult:
        """Chat with tools, returning stop_reason if available.

        Default implementation delegates to chat_with_tools and returns None for stop_reason.
        Subclasses should override this to extract stop_reason from API responses.

        @param messages List of conversation messages
        @param tools List of available tools
        @param system_prompt System prompt to prepend
        @return ChatResult with text, tool_calls, and optional stop_reason
        """
        text, tool_calls = await self.chat_with_tools(messages, tools, system_prompt)
        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=None)

    async def close(self):
        """Release the underlying HTTP client if any. Subclasses may override."""
        client = getattr(self, "_client", None)
        if client is not None and hasattr(client, "aclose"):
            await client.aclose()
            self._client = None

    # ── Shared prompt-injection fallback (used when native tool calling fails) ──

    def _build_tool_prompt(self, tools: list[dict]) -> str:
        """Build the XML tool-calling prompt injected into the system prompt."""
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
{_TOOL_CALL_CLOSE}

You can make multiple tool calls by using multiple  <tool_call> blocks.

Available tools:
{''.join(tool_descriptions)}
"""

    def _parse_tool_calls_from_response(self, response: str) -> list[dict]:
        """Parse <tool_call> blocks from a model response. Override for custom syntax."""
        tool_calls = []
        for match in _TOOL_CALL_PATTERN.finditer(response):
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

    async def _chat_with_tool_prompt(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = None
    ) -> tuple[str, list[dict]]:
        """Fallback: use prompt injection for tool calling."""
        tool_prompt = self._build_tool_prompt(tools)
        enhanced_prompt = f"{system_prompt}\n\n{tool_prompt}" if system_prompt else tool_prompt

        response = await self.chat(messages, enhanced_prompt)
        tool_calls = self._parse_tool_calls_from_response(response)

        return response, tool_calls

    # ── Shared OpenAI-format tool_calls extraction ──

    @staticmethod
    def _extract_openai_tool_calls(message: dict) -> list[dict]:
        """Extract tool calls from an OpenAI-format response message.

        Handles malformed argument JSON via robust parsing and repair.
        """
        from src.error.json_repair import robust_json_parse, try_repair_malformed_json

        tool_calls = []
        for call in message.get("tool_calls", []):
            func = call.get("function", {})
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                parsed = robust_json_parse(args)
                if "__parse_error__" in parsed:
                    repaired = try_repair_malformed_json(args)
                    if repaired is not None:
                        args = repaired
                    else:
                        args = {"raw": args}
                else:
                    args = parsed
            tool_calls.append({
                "name": func.get("name"),
                "arguments": args,
                "id": call.get("id") or f"call_{len(tool_calls)}",
            })
        return tool_calls

    @abstractmethod
    def get_name(self) -> str:
        """Return adapter name

        @return Adapter name string
        """
        pass

    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether the model supports streaming

        @return True if streaming is supported
        """
        pass

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        system_prompt: str = None
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat with tool support.

        This method yields StreamEvent objects as the model generates output,
        allowing real-time display of the model's response.

        @param messages List of conversation messages
        @param tools List of available tools (optional)
        @param system_prompt System prompt to prepend
        @yield StreamEvent objects representing the streaming response
        """
        # Default implementation falls back to non-streaming
        # Subclasses should override for actual streaming support
        result = await self.chat_with_tools_and_stop_reason(messages, tools, system_prompt)
        response = result.text
        tool_calls = result.tool_calls

        # Emit text delta
        if response:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, content=response)

        # Emit tool calls if any
        if tool_calls:
            for tc in tool_calls:
                yield StreamEvent(
                    type=StreamEventType.TOOL_USE_COMPLETE,
                    tool_name=tc.get("name"),
                    tool_id=tc.get("id"),
                    tool_input=tc.get("arguments"),
                    tool_calls=tool_calls
                )

        yield StreamEvent(type=StreamEventType.MESSAGE_STOP, stop_reason=result.stop_reason)