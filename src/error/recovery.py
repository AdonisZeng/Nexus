"""
Error Recovery Strategies - handles different error types with appropriate recovery.
"""
import asyncio
import random
from enum import Enum
from typing import Callable, Awaitable, Optional

from .constants import (
    BACKOFF_BASE_DELAY,
    BACKOFF_MAX_DELAY,
    MAX_RECOVERY_ATTEMPTS,
    MAX_OUTPUT_RECOVERY_ATTEMPTS,
    CONTINUATION_MESSAGE,
)


class RecoveryStrategy(Enum):
    """Recovery strategies for different error types."""
    CONTINUE = "continue"           # Inject continuation message, retry
    COMPACT_AND_RETRY = "compact"  # Compress context, then retry
    BACKOFF_RETRY = "backoff"      # Exponential backoff with jitter
    FAIL = "fail"                  # No recovery possible, fail


class ErrorRecovery:
    """Handles error recovery with various strategies."""

    @staticmethod
    def calculate_backoff_delay(attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds
        """
        delay = min(BACKOFF_BASE_DELAY * (2 ** attempt), BACKOFF_MAX_DELAY)
        jitter = random.uniform(0, 1)
        return delay + jitter

    @staticmethod
    def should_retry_max_tokens(recovery_count: int) -> bool:
        """Check if we should retry after max_tokens.

        Args:
            recovery_count: Number of previous max_tokens recovery attempts

        Returns:
            True if should retry, False if exhausted
        """
        return recovery_count < MAX_OUTPUT_RECOVERY_ATTEMPTS

    @staticmethod
    async def handle_max_tokens(
        messages: list,
        recovery_count: int
    ) -> tuple[bool, int]:
        """Handle max_tokens recovery by injecting continuation message.

        Args:
            messages: Conversation messages list
            recovery_count: Current recovery attempt count

        Returns:
            Tuple of (should_retry, new_recovery_count)
        """
        if ErrorRecovery.should_retry_max_tokens(recovery_count):
            new_count = recovery_count + 1
            messages.append({"role": "user", "content": CONTINUATION_MESSAGE})
            return True, new_count
        return False, recovery_count

    @staticmethod
    async def handle_with_strategy(
        error: Exception,
        func: Callable[[], Awaitable],
        strategy: RecoveryStrategy,
        attempt: int = 0,
        messages: Optional[list] = None,
        max_retries: int = MAX_RECOVERY_ATTEMPTS,
    ) -> tuple[bool, Optional[Exception]]:
        """Execute recovery strategy.

        Args:
            error: The error that occurred
            func: Async function to retry
            strategy: Recovery strategy to use
            attempt: Current attempt number
            messages: Conversation messages for CONTINUE strategy
            max_retries: Maximum retry attempts

        Returns:
            Tuple of (success, final_error_or_none)
        """
        if attempt >= max_retries:
            return False, error

        if strategy == RecoveryStrategy.CONTINUE:
            if messages is not None:
                should_retry, new_count = await ErrorRecovery.handle_max_tokens(
                    messages, attempt
                )
                if should_retry:
                    try:
                        await func()
                        return True, None
                    except Exception as e:
                        return await ErrorRecovery.handle_with_strategy(
                            e, func, strategy, attempt + 1, messages, max_retries
                        )
            return False, error

        elif strategy == RecoveryStrategy.BACKOFF_RETRY:
            delay = ErrorRecovery.calculate_backoff_delay(attempt)
            await asyncio.sleep(delay)
            try:
                await func()
                return True, None
            except Exception as e:
                return await ErrorRecovery.handle_with_strategy(
                    e, func, strategy, attempt + 1, messages, max_retries
                )

        elif strategy == RecoveryStrategy.COMPACT_AND_RETRY:
            # Context compaction should be handled by caller
            # This just signals that compaction should happen
            return False, error

        elif strategy == RecoveryStrategy.FAIL:
            return False, error

        return False, error


class BackoffCalculator:
    """Utility class for calculating backoff delays with various strategies."""

    @staticmethod
    def simple(attempt: int) -> float:
        """Simple exponential backoff: 2^attempt seconds."""
        return 2 ** attempt

    @staticmethod
    def with_jitter(attempt: int) -> float:
        """Exponential backoff with jitter (recommended)."""
        return ErrorRecovery.calculate_backoff_delay(attempt)

    @staticmethod
    def capped(attempt: int, cap: float = BACKOFF_MAX_DELAY) -> float:
        """Exponential backoff with cap, no jitter."""
        return min(BACKOFF_BASE_DELAY * (2 ** attempt), cap)
