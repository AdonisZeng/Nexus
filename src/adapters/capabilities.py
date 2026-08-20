"""Model capabilities and inference for different model providers."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelCapabilities:
    """Model capabilities configuration."""

    supports_tools: bool = True
    supports_streaming: bool = True
    supports_strict_schema: bool = True
    supports_developer_role: bool = False
    thinking_format: Optional[str] = None
    tool_schema_profile: Optional[str] = None
    requires_tool_result_name: bool = False
    fallback_to_prompt_injection: bool = False
    # Additional compatibility settings
    tool_call_arguments_encoding: Optional[str] = None  # "html-entities" for xAI/Grok
    requires_tool_call_repair: bool = False  # For models like Kimi, GLM


@dataclass
class ModelCompatConfig:
    """Model compatibility configuration

    Used to handle special behaviors of different LLM providers.
    """

    # 功能支持
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_developer_role: bool = False
    supports_strict_mode: bool = False
    supports_usage_in_streaming: bool = False

    # 参数编码
    tool_call_arguments_encoding: Optional[str] = None  # "html-entities"

    # 修复选项
    requires_tool_call_repair: bool = False  # Kimi, GLM等模型
    fallback_to_prompt_injection: bool = False

    # 超时配置
    request_timeout: float = 300.0

    # 预设配置
    PRESETS: dict = field(default_factory=lambda: {
        "xai": {
            "tool_call_arguments_encoding": "html-entities",
            "supports_strict_mode": False,
        },
        "kimi": {
            "requires_tool_call_repair": True,
            "supports_strict_mode": False,
        },
        "glm": {
            "requires_tool_call_repair": True,
        },
        "ollama": {
            "fallback_to_prompt_injection": True,
        },
    })

    @classmethod
    def from_model_name(cls, model: str, provider: str = None) -> "ModelCompatConfig":
        """Create config based on model name

        @param model: Model name
        @param provider: Provider name (optional)
        @return: ModelCompatConfig instance
        """
        config = cls()

        model_lower = model.lower() if model else ""
        provider_lower = (provider or "").lower()

        # Apply presets
        for preset_name, preset in config.PRESETS.items():
            if preset_name in model_lower or preset_name in provider_lower:
                for key, value in preset.items():
                    setattr(config, key, value)

        return config


# Model capability hints based on model family
MODEL_CAPABILITY_HINTS = {
    "qwen": {
        "supports_tools": True,
        "thinking_format": "qwen-chat-template",
    },
    "llama": {
        "supports_tools": False,  # Most Llama models don't support native tool calling
    },
    "mistral": {
        "supports_tools": True,
        "requires_tool_result_name": True,
    },
    "mixtral": {
        "supports_tools": True,
        "requires_tool_result_name": True,
    },
    "deepseek": {
        "supports_tools": True,
        "thinking_format": "deepseek",
    },
    "codestral": {
        "supports_tools": True,
    },
    "gemma": {
        "supports_tools": False,
    },
    "phi": {
        "supports_tools": True,
    },
    "hermes": {
        "supports_tools": True,  # Hermes series is tuned for tool calling
    },
    "aya": {
        "supports_tools": True,
    },
    "command": {
        "supports_tools": True,  # Cohere Command models
    },
    "claude": {
        "supports_tools": True,
    },
    "gpt": {
        "supports_tools": True,
    },
    "xai": {
        "supports_tools": True,
        "tool_call_arguments_encoding": "html-entities",
    },
    "grok": {
        "supports_tools": True,
        "tool_call_arguments_encoding": "html-entities",
    },
    "kimi": {
        "supports_tools": True,
        "requires_tool_call_repair": True,
    },
    "glm": {
        "supports_tools": True,
        "requires_tool_call_repair": True,
    },
    "minimax": {
        "supports_tools": True,
    },
}


def infer_capabilities_from_model_name(model_id: str) -> dict:
    """
    Infer model capabilities based on model name.

    Args:
        model_id: Model identifier (e.g., "qwen-2.5-72b", "llama3.1")

    Returns:
        Dictionary of capability hints
    """
    if not model_id:
        return {}

    model_lower = model_id.lower()

    for hint, caps in MODEL_CAPABILITY_HINTS.items():
        if hint in model_lower:
            return caps.copy()

    return {}


def merge_capabilities(
    explicit: Optional[dict] = None,
    inferred: Optional[dict] = None,
) -> ModelCapabilities:
    """
    Merge explicit and inferred capabilities.

    Explicit config takes precedence over inferred.

    Args:
        explicit: Explicit capabilities from config
        inferred: Inferred capabilities from model name

    Returns:
        Merged ModelCapabilities
    """
    merged = {**(inferred or {}), **(explicit or {})}

    return ModelCapabilities(
        supports_tools=merged.get("supports_tools", True),
        supports_streaming=merged.get("supports_streaming", True),
        supports_strict_schema=merged.get("supports_strict_schema", True),
        supports_developer_role=merged.get("supports_developer_role", False),
        thinking_format=merged.get("thinking_format"),
        tool_schema_profile=merged.get("tool_schema_profile"),
        requires_tool_result_name=merged.get("requires_tool_result_name", False),
        fallback_to_prompt_injection=merged.get("fallback_to_prompt_injection", False),
        tool_call_arguments_encoding=merged.get("tool_call_arguments_encoding"),
        requires_tool_call_repair=merged.get("requires_tool_call_repair", False),
    )