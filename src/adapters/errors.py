"""Shared error handling utilities for model adapters.

This module now re-exports from src.error for backward compatibility.
The canonical location for these utilities is src.error.json_repair.
"""
# Re-export from src.error.json_repair for backward compatibility
from src.error.json_repair import (
    decode_html_entities,
    decode_html_entities_in_object,
    extract_balanced_json_prefix,
    try_repair_malformed_json,
    robust_json_parse,
    extract_tool_calls_from_message,
    check_tool_call_parse_errors_and_retry,
    validate_openai_response,
    handle_http_errors,
)

__all__ = [
    "decode_html_entities",
    "decode_html_entities_in_object",
    "extract_balanced_json_prefix",
    "try_repair_malformed_json",
    "robust_json_parse",
    "extract_tool_calls_from_message",
    "check_tool_call_parse_errors_and_retry",
    "validate_openai_response",
    "handle_http_errors",
]
