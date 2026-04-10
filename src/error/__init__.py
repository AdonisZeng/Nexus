"""
Error Recovery Module - centralized error handling for Nexus.

Provides:
- Error classification (ErrorType, APIErrorClassifier)
- Recovery strategies (RecoveryStrategy, ErrorRecovery)
- Retry with backoff (execute_with_retry)
- JSON repair (try_repair_malformed_json, robust_json_parse)
- Centralized constants

Usage:
    from src.error import (
        ErrorType,
        RecoveryStrategy,
        ErrorRecovery,
        execute_with_retry,
        MAX_RECOVERY_ATTEMPTS,
    )
"""

# Constants
from .constants import (
    MAX_RECOVERY_ATTEMPTS,
    MAX_OUTPUT_RECOVERY_ATTEMPTS,
    MAX_RETRIES_PER_TASK,
    BACKOFF_BASE_DELAY,
    BACKOFF_MAX_DELAY,
    TOKEN_THRESHOLD,
    CONTEXT_COMPRESS_THRESHOLD,
    CONTINUATION_MESSAGE,
    TOOL_RESULT_PLACEHOLDER,
    MICRO_COMPACT_THRESHOLD,
)

# Error classification
from .classifier import (
    ErrorType,
    APIErrorClassifier,
)

# Recovery strategies
from .recovery import (
    RecoveryStrategy,
    ErrorRecovery,
    BackoffCalculator,
)

# Retry utilities
from .retry import (
    execute_with_retry,
    execute_with_retry_and_recovery,
    retry_decorator,
)

# JSON repair
from .json_repair import (
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
    # Constants
    "MAX_RECOVERY_ATTEMPTS",
    "MAX_OUTPUT_RECOVERY_ATTEMPTS",
    "MAX_RETRIES_PER_TASK",
    "BACKOFF_BASE_DELAY",
    "BACKOFF_MAX_DELAY",
    "TOKEN_THRESHOLD",
    "CONTEXT_COMPRESS_THRESHOLD",
    "CONTINUATION_MESSAGE",
    "TOOL_RESULT_PLACEHOLDER",
    "MICRO_COMPACT_THRESHOLD",
    # Classification
    "ErrorType",
    "APIErrorClassifier",
    # Recovery
    "RecoveryStrategy",
    "ErrorRecovery",
    "BackoffCalculator",
    # Retry
    "execute_with_retry",
    "execute_with_retry_and_recovery",
    "retry_decorator",
    # JSON repair
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
