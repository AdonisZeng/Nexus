"""ModelProvider interface for dependency injection.

This interface allows components to access the current model adapter
without relying on global state.
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .base import ModelAdapter


class ModelProvider(ABC):
    """Interface for model adapter access.

    Components that need access to the current model adapter should
    accept a ModelProvider instance rather than using global state.

    Example:
        class MyTool(Tool):
            def __init__(self, provider: ModelProvider):
                self._provider = provider

            async def execute(self, **kwargs):
                adapter = self._provider.get_adapter()
                if adapter is None:
                    raise RuntimeError("No model adapter configured")
                # ... use adapter
    """

    @abstractmethod
    def get_adapter(self) -> Optional["ModelAdapter"]:
        """Get the current model adapter.

        Returns:
            The current ModelAdapter instance, or None if not set.
        """
        pass

    @abstractmethod
    def set_adapter(self, adapter: "ModelAdapter") -> None:
        """Set the current model adapter.

        Args:
            adapter: The ModelAdapter to set as current.
        """
        pass
