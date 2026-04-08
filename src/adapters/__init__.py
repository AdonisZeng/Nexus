"""Model adapters

This module provides a unified interface for various LLM providers.
Adapters self-register via __init_subclass__ when imported.
"""
from .base import ModelAdapter
from .registry import AdapterRegistry
from .provider import ModelProvider

# Import all adapters to trigger __init_subclass__ auto-registration
from .anthropic import AnthropicAdapter
from .openai import OpenAIAdapter
from .ollama import OllamaAdapter
from .lmstudio import LMStudioAdapter
from .custom import CustomAdapter
from .xai import XAIAdapter

__all__ = [
    "ModelAdapter",
    "ModelProvider",
    "AdapterRegistry",
    # Backward compatibility - deprecated
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


# ============================================================================
# Backward Compatibility Layer
# ============================================================================
# Global current adapter for legacy subagent access
_current_adapter: ModelAdapter = None


def set_current_adapter(adapter: ModelAdapter) -> None:
    """Set the current global adapter (used by legacy SubagentTool).

    DEPRECATED: Use dependency injection via ModelProvider instead.
    """
    global _current_adapter
    _current_adapter = adapter


def get_current_adapter() -> ModelAdapter:
    """Get the current global adapter (used by legacy SubagentTool).

    DEPRECATED: Use dependency injection via ModelProvider instead.
    """
    return _current_adapter


def create_adapter(adapter_type: str, **kwargs) -> ModelAdapter:
    """Factory function to create model adapters.

    DEPRECATED: Use AdapterRegistry.create() instead.

    This function is kept for backward compatibility.
    """
    # Use registry to create adapter
    return AdapterRegistry.create(adapter_type, kwargs)
