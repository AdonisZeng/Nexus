"""Model adapters"""
from .base import ModelAdapter
from .anthropic import AnthropicAdapter
from .openai import OpenAIAdapter
from .ollama import OllamaAdapter
from .lmstudio import LMStudioAdapter
from .custom import CustomAdapter
from .xai import XAIAdapter

__all__ = [
    "ModelAdapter",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "OllamaAdapter",
    "LMStudioAdapter",
    "CustomAdapter",
    "XAIAdapter",
    "create_adapter",
    "set_current_adapter",
    "get_current_adapter",
]

# Global current adapter for subagent access
_current_adapter: ModelAdapter = None


def set_current_adapter(adapter: ModelAdapter) -> None:
    """Set the current global adapter (used by SubagentTool)"""
    global _current_adapter
    _current_adapter = adapter


def get_current_adapter() -> ModelAdapter:
    """Get the current global adapter (used by SubagentTool)"""
    return _current_adapter


def create_adapter(adapter_type: str, **kwargs) -> ModelAdapter:
    """Factory function to create model adapters"""
    adapters = {
        "anthropic": AnthropicAdapter,
        "openai": OpenAIAdapter,
        "ollama": OllamaAdapter,
        "lmstudio": LMStudioAdapter,
        "custom": CustomAdapter,
        "xai": XAIAdapter,
    }

    adapter_class = adapters.get(adapter_type.lower())
    if not adapter_class:
        raise ValueError(f"Unknown adapter type: {adapter_type}")

    return adapter_class(**kwargs)
