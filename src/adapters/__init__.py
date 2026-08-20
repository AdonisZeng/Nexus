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
from .minimax import MinimaxAdapter

__all__ = [
    "ModelAdapter",
    "ModelProvider",
    "AdapterRegistry",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "OllamaAdapter",
    "LMStudioAdapter",
    "CustomAdapter",
    "XAIAdapter",
    "MinimaxAdapter",
    "create_adapter",
]


# ============================================================================
# Backward Compatibility
# ============================================================================
def create_adapter(adapter_type: str, **kwargs) -> ModelAdapter:
    """Factory function to create model adapters.

    DEPRECATED: Use AdapterRegistry.create() instead.
    """
    return AdapterRegistry.create(adapter_type, kwargs)
