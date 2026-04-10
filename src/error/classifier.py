"""
API Error Classification - maps errors to recovery strategies.
"""
from enum import Enum
from typing import Optional

from .recovery import RecoveryStrategy


class ErrorType(Enum):
    """Types of errors that can occur during LLM API calls."""
    CONTEXT_TOO_LARGE = "context_too_large"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    MAX_TOKENS = "max_tokens"
    AUTHENTICATION = "authentication"
    NOT_FOUND = "not_found"
    UNRECOVERABLE = "unrecoverable"


class APIErrorClassifier:
    """Classifies API errors and determines appropriate recovery strategies."""

    # HTTP status codes mapped to error types
    STATUS_CODE_MAP = {
        401: ErrorType.AUTHENTICATION,
        403: ErrorType.AUTHENTICATION,
        404: ErrorType.NOT_FOUND,
        429: ErrorType.RATE_LIMIT,
    }

    # Error message patterns mapped to error types
    PATTERN_MAP = {
        ("overlong_prompt", "prompt_too_long", "prompt too long"): ErrorType.CONTEXT_TOO_LARGE,
        ("rate_limit", "rate limit", "429", "too many requests"): ErrorType.RATE_LIMIT,
        ("connection", "timeout", "network", "refused"): ErrorType.TRANSIENT,
        ("max_tokens", "output limit", "token limit"): ErrorType.MAX_TOKENS,
    }

    @classmethod
    def from_http_status(cls, status_code: int) -> ErrorType:
        """Classify error from HTTP status code."""
        return cls.STATUS_CODE_MAP.get(status_code, ErrorType.UNRECOVERABLE)

    @classmethod
    def from_exception(cls, error: Exception) -> ErrorType:
        """Classify error from exception object.

        Args:
            error: Any exception from API call

        Returns:
            ErrorType classification
        """
        error_str = str(error).lower()

        # Check status code in error message (common format: "401: ...")
        for code in cls.STATUS_CODE_MAP:
            if str(code) in error_str:
                return cls.STATUS_CODE_MAP[code]

        # Check text patterns
        for patterns, error_type in cls.PATTERN_MAP.items():
            if any(p in error_str for p in patterns):
                return error_type

        # Classify by exception type
        if isinstance(error, (ConnectionError, TimeoutError, OSError)):
            return ErrorType.TRANSIENT

        # For unknown exceptions, treat as potentially transient (retry by default)
        # Only mark truly unrecoverable errors (like Authentication) as FAIL
        return ErrorType.TRANSIENT

    @classmethod
    def get_recovery_strategy(cls, error_type: ErrorType) -> RecoveryStrategy:
        """Get the recovery strategy for an error type.

        Args:
            error_type: Classified error type

        Returns:
            RecoveryStrategy to use
        """
        strategy_map = {
            ErrorType.CONTEXT_TOO_LARGE: RecoveryStrategy.COMPACT_AND_RETRY,
            ErrorType.RATE_LIMIT: RecoveryStrategy.BACKOFF_RETRY,
            ErrorType.TRANSIENT: RecoveryStrategy.BACKOFF_RETRY,
            ErrorType.MAX_TOKENS: RecoveryStrategy.CONTINUE,
            ErrorType.AUTHENTICATION: RecoveryStrategy.FAIL,
            ErrorType.NOT_FOUND: RecoveryStrategy.FAIL,
            ErrorType.UNRECOVERABLE: RecoveryStrategy.FAIL,
        }
        return strategy_map.get(error_type, RecoveryStrategy.FAIL)

    @classmethod
    def classify_and_get_strategy(
        cls, error: Exception
    ) -> tuple[ErrorType, RecoveryStrategy]:
        """Convenience method to classify and get strategy in one call.

        Args:
            error: Exception to classify

        Returns:
            Tuple of (ErrorType, RecoveryStrategy)
        """
        error_type = cls.from_exception(error)
        strategy = cls.get_recovery_strategy(error_type)
        return error_type, strategy
