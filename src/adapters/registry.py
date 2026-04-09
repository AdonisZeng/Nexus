"""Adapter registry for self-registering model adapters"""
from typing import TYPE_CHECKING, Optional, Type

if TYPE_CHECKING:
    from .base import ModelAdapter

import logging

logger = logging.getLogger("adapters.registry")


class AdapterRegistry:
    """Registry for model adapters with self-registration.

    Adapters self-register via __init_subclass__ when subclassed.
    """

    _adapters: dict[str, Type["ModelAdapter"]] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type["ModelAdapter"]) -> None:
        """Register an adapter class.

        Args:
            name: Provider name (e.g., "anthropic", "openai")
            adapter_class: The adapter class to register
        """
        name = name.lower()
        if name in cls._adapters:
            logger.warning(f"AdapterRegistry: Overwriting existing adapter '{name}'")
        cls._adapters[name] = adapter_class
        logger.debug(f"AdapterRegistry: Registered adapter '{name}'")

    @classmethod
    def get(cls, name: str) -> Optional[Type["ModelAdapter"]]:
        """Get adapter class by name.

        Args:
            name: Provider name

        Returns:
            Adapter class or None if not found
        """
        return cls._adapters.get(name.lower())

    @classmethod
    def list_providers(cls) -> list[str]:
        """List all registered provider names.

        Returns:
            List of provider names
        """
        return list(cls._adapters.keys())

    @classmethod
    def create(cls, provider: str, config: dict) -> "ModelAdapter":
        """Create an adapter instance from config.

        Args:
            provider: Provider name (e.g., "anthropic")
            config: Full config dict (contains models.*)

        Returns:
            Adapter instance

        Raises:
            ValueError: If provider is not registered
        """
        from .base import ModelAdapter

        provider_key = provider.lower()
        adapter_class = cls.get(provider_key)
        if not adapter_class:
            available = ", ".join(sorted(cls._adapters.keys()))
            raise ValueError(
                f"Unknown provider: '{provider}'. Available: {available}"
            )

        # Provider-specific config is nested under provider name
        provider_config = config.get(provider_key, {})

        # Use from_config if available, otherwise construct directly
        if hasattr(adapter_class, "from_config"):
            return adapter_class.from_config(provider_config)
        else:
            return adapter_class(**provider_config)

    @classmethod
    def create_all_registered(cls) -> None:
        """Trigger registration for all built-in adapters.

        Call this once at module init to ensure all adapters are registered.
        """
        # Import all adapters to trigger __init_subclass__ registration
        from . import anthropic
        from . import openai
        from . import ollama
        from . import lmstudio
        from . import custom
        from . import xai
        from . import minimax
