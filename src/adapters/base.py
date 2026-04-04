"""Model adapter base class"""
from abc import ABC, abstractmethod
from typing import Any, Optional, AsyncIterator, Dict
from dataclasses import dataclass
from enum import Enum, auto

from .capabilities import ModelCapabilities, merge_capabilities, infer_capabilities_from_model_name

from src.utils import get_logger

logger = get_logger("adapters.base")


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


class StreamingToolCallBuffer:
    """流式工具调用参数缓冲区

    用于收集和修复流式响应中的不完整工具调用参数。
    """

    def __init__(self):
        self.buffers: Dict[str, str] = {}  # tool_id -> partial_json
        self.repaired: Dict[str, dict] = {}  # tool_id -> repaired_args

    def append(self, tool_id: str, delta: str) -> None:
        """追加增量内容

        @param tool_id: Tool call ID
        @param delta: Incremental content
        """
        self.buffers[tool_id] = self.buffers.get(tool_id, "") + delta

    def try_repair(self, tool_id: str) -> Optional[dict]:
        """尝试修复不完整的 JSON

        @param tool_id: Tool call ID
        @return: Repaired arguments or None
        """
        from .errors import try_repair_malformed_json

        raw = self.buffers.get(tool_id, "")
        if not raw:
            return None

        # Try to repair
        result = try_repair_malformed_json(raw)
        if result is not None:
            self.repaired[tool_id] = result

        return result

    def finalize(self, tool_id: str) -> dict:
        """最终化工具调用参数

        @param tool_id: Tool call ID
        @return: Finalized arguments
        """
        # Return repaired args, or try one last repair
        if tool_id in self.repaired:
            return self.repaired[tool_id]

        result = self.try_repair(tool_id)
        if result is not None:
            return result

        # Complete failure, return raw content
        return {"__raw__": self.buffers.get(tool_id, ""), "__error__": "parse_failed"}

    def clear(self, tool_id: str = None) -> None:
        """清理缓冲区

        @param tool_id: Specific tool ID to clear, or None to clear all
        """
        if tool_id:
            self.buffers.pop(tool_id, None)
            self.repaired.pop(tool_id, None)
        else:
            self.buffers.clear()
            self.repaired.clear()


class ModelAdapter(ABC):
    """Base class for model adapters"""

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
        response, tool_calls = await self.chat_with_tools(messages, tools, system_prompt)

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

        yield StreamEvent(type=StreamEventType.MESSAGE_STOP)