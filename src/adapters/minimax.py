"""MiniMax model adapter — preset of CustomAdapter with Anthropic-protocol endpoint."""
import os
from .custom import CustomAdapter


class MinimaxAdapter(CustomAdapter):
    """MiniMax API via Anthropic-compatible endpoint.

    Environment variable: MINIMAX_API_KEY
    Default base URL: https://api.minimaxi.com/anthropic
    Default model: MiniMax-M2.7
    """

    PROVIDER_NAME = "minimax"

    @classmethod
    def from_config(cls, config: dict) -> "MinimaxAdapter":
        """Create adapter from config dict.

        @param config Provider config dict (models.minimax section)
        @return MinimaxAdapter instance
        """
        return cls(
            base_url=config.get("base_url", "https://api.minimaxi.com/anthropic"),
            api_key=config.get("api_key") or os.environ.get("MINIMAX_API_KEY"),
            model=config.get("model", "MiniMax-M2.7"),
            compat=config.get("compat"),
            api_protocol="anthropic",
        )
