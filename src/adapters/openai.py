"""OpenAI adapter (OpenAI-compatible httpx implementation)."""
import os

from .openai_compat import OpenAICompatAdapter


class OpenAIAdapter(OpenAICompatAdapter):
    """Adapter for OpenAI models via the chat-completions API."""

    PROVIDER_NAME = "openai"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            api_key=config.get("api_key"),
            base_url=config.get("base_url", "https://api.openai.com/v1"),
            model=config.get("model", "gpt-4o"),
            compat=config.get("compat"),
        )

    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        compat: dict = None
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            model=model,
            compat=compat,
        )

    def get_name(self) -> str:
        return self.model or "gpt-4o"
