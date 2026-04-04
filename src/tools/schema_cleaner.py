"""Schema cleaner for different model providers."""
from typing import Any, Set


class SchemaCleaner:
    """Clean JSON Schema for different model providers that have limited support."""

    # Keywords not supported by Gemini
    GEMINI_UNSUPPORTED_KEYWORDS: Set[str] = {
        "patternProperties",
        "additionalProperties",
        "$ref",
        "minLength",
        "maxLength",
        "format",
        "pattern",
        "contentMediaType",
        "contentEncoding",
    }

    # Keywords not supported by xAI (Grok)
    XAI_UNSUPPORTED_KEYWORDS: Set[str] = {
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minContains",
        "maxContains",
    }

    @classmethod
    def clean_for_gemini(cls, schema: dict) -> dict:
        """Clean schema for Gemini models."""
        return cls._strip_keywords(schema, cls.GEMINI_UNSUPPORTED_KEYWORDS)

    @classmethod
    def clean_for_xai(cls, schema: dict) -> dict:
        """Clean schema for xAI (Grok) models."""
        return cls._strip_keywords(schema, cls.XAI_UNSUPPORTED_KEYWORDS)

    @classmethod
    def _strip_keywords(cls, schema: Any, keywords: Set[str]) -> Any:
        """Recursively strip unsupported keywords from schema."""
        if not schema or not isinstance(schema, dict):
            return schema

        cleaned = {}
        for key, value in schema.items():
            if key in keywords:
                continue

            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {
                    k: cls._strip_keywords(v, keywords)
                    for k, v in value.items()
                }
            elif key == "items" and isinstance(value, dict):
                cleaned[key] = cls._strip_keywords(value, keywords)
            elif key in ("anyOf", "oneOf", "allOf") and isinstance(value, list):
                cleaned[key] = [cls._strip_keywords(item, keywords) for item in value]
            elif key == "definitions":
                cleaned[key] = {
                    k: cls._strip_keywords(v, keywords)
                    for k, v in value.items()
                }
            else:
                cleaned[key] = value

        return cleaned

    @classmethod
    def clean_for_provider(
        cls,
        schema: dict,
        provider: str,
        tool_schema_profile: str = None
    ) -> dict:
        """
        Clean schema based on provider.

        Args:
            schema: Input JSON schema
            provider: Provider name (e.g., "google", "gemini", "xai")
            tool_schema_profile: Specific profile to use

        Returns:
            Cleaned schema
        """
        provider_lower = (provider or "").lower()

        if provider_lower in ("google", "gemini"):
            return cls.clean_for_gemini(schema)

        if tool_schema_profile == "xai":
            return cls.clean_for_xai(schema)

        return schema