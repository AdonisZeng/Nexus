"""xAI Grok adapter (OpenAI-compatible httpx implementation).

Grok models HTML-encode tool call arguments (&quot; instead of "), —
the shared extraction path decodes them when the capability is set.
"""
import os

from .openai_compat import OpenAICompatAdapter


class XAIAdapter(OpenAICompatAdapter):
    """Adapter for xAI Grok models."""

    PROVIDER_NAME = "xai"

    @classmethod
    def from_config(cls, config: dict):
        """Create adapter from config dict."""
        return cls(
            api_key=config.get("api_key"),
            base_url=config.get("base_url", "https://api.x.ai/v1"),
            model=config.get("model", "grok-2"),
            compat=config.get("compat"),
        )

    def __init__(
        self,
        api_key: str = None,
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-2",
        compat: dict = None
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key or os.environ.get("XAI_API_KEY"),
            model=model,
            compat=compat,
        )

        # Enable HTML entity decoding for xAI/Grok models
        capabilities = self.get_capabilities()
        if capabilities.tool_call_arguments_encoding is None:
            capabilities.tool_call_arguments_encoding = "html-entities"

    def get_name(self) -> str:
        return self.model or "grok-2"
