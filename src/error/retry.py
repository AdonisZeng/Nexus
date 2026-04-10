"""
Retry utilities with exponential backoff and jitter.
"""
import asyncio
import logging
from typing import Callable, Any, Awaitable, Optional, TypeVar, Type

from .constants import MAX_RECOVERY_ATTEMPTS, BACKOFF_BASE_DELAY, BACKOFF_MAX_DELAY
from .recovery import ErrorRecovery, RecoveryStrategy
from .classifier import APIErrorClassifier, ErrorType

logger = logging.getLogger("Nexus")

T = TypeVar('T')


async def execute_with_retry(
    func: Callable[[], Awaitable[T]],
    max_retries: int = MAX_RECOVERY_ATTEMPTS,
    error_types: tuple = (Exception,),
    on_retry: Optional[Callable[[Exception, int], Awaitable[None]]] = None,
) -> tuple[Optional[T], bool]:
    """Execute an async function with exponential backoff and jitter retry.

    Args:
        func: Async function to execute
        max_retries: Maximum number of retry attempts (default 3)
        error_types: Tuple of exception types to catch and retry
        on_retry: Optional async callback called on each retry with (error, attempt)

    Returns:
        Tuple of (result, success). If success is False, result is None.

    Example:
        result, success = await execute_with_retry(
            lambda: some_async_function(),
            max_retries=3
        )
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await func()
            if attempt > 0:
                logger.info(f"Retry {attempt} succeeded after previous failure")
            return result, True

        except error_types as e:
            last_error = e

            if attempt < max_retries:
                # Classify error once outside retry loop for efficiency
                error_type, strategy = APIErrorClassifier.classify_and_get_strategy(e)

                if strategy != RecoveryStrategy.BACKOFF_RETRY:
                    logger.warning(
                        f"Non-retryable error type {error_type.value}: {e}"
                    )
                    break

                delay = ErrorRecovery.calculate_backoff_delay(attempt)

                if on_retry:
                    await on_retry(e, attempt)

                logger.warning(
                    f"Attempt {attempt + 1} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"All {max_retries + 1} attempts failed: {e}"
                )

    return None, False


async def execute_with_retry_and_recovery(
    func: Callable[[], Awaitable[tuple]],
    max_retries: int = MAX_RECOVERY_ATTEMPTS,
    messages: Optional[list] = None,
) -> tuple[Any, list, bool]:
    """Execute with retry and recovery strategies for different error types.

    This is a more advanced version that handles different error types
    with appropriate recovery strategies.

    Args:
        func: Async function that returns (response, tool_calls) tuple
        max_retries: Maximum retry attempts
        messages: Conversation messages for context recovery

    Returns:
        Tuple of (response, tool_calls, success)
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await func()
            if attempt > 0:
                logger.info(f"Attempt {attempt} succeeded after previous failure")
            return result, True

        except Exception as e:
            last_error = e
            error_type, strategy = APIErrorClassifier.classify_and_get_strategy(e)

            if strategy == RecoveryStrategy.CONTINUE and messages:
                # Handle max_tokens by injecting continuation
                should_retry, new_count = await ErrorRecovery.handle_max_tokens(
                    messages, attempt
                )
                if should_retry:
                    logger.info(
                        f"max_tokens recovery: injecting continuation "
                        f"(attempt {new_count}/{MAX_RECOVERY_ATTEMPTS})"
                    )
                    continue

            if strategy == RecoveryStrategy.BACKOFF_RETRY and attempt < max_retries:
                delay = ErrorRecovery.calculate_backoff_delay(attempt)
                logger.warning(
                    f"Error type {error_type.value}: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
                continue

            # FAIL or exhausted retries
            if attempt >= max_retries:
                logger.error(
                    f"All {max_retries + 1} attempts failed with "
                    f"error type {error_type.value}: {e}"
                )
            break

    return None, [], False


def retry_decorator(
    max_retries: int = MAX_RECOVERY_ATTEMPTS,
    error_types: tuple = (Exception,),
):
    """Decorator for adding retry logic to async functions.

    Args:
        max_retries: Maximum retry attempts
        error_types: Exception types to catch

    Example:
        @retry_decorator(max_retries=3)
        async def my_function():
            ...
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(*args, **kwargs) -> T:
            result, success = await execute_with_retry(
                lambda: func(*args, **kwargs),
                max_retries=max_retries,
                error_types=error_types,
            )
            if not success:
                raise RuntimeError(f"Function {func.__name__} failed after {max_retries} retries")
            return result
        return wrapper
    return decorator
