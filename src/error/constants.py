"""
Error recovery constants - centralized configuration for error handling.

All error-related constants should be defined here to avoid duplication
across the codebase.
"""

# Recovery limits
MAX_RECOVERY_ATTEMPTS = 3
MAX_OUTPUT_RECOVERY_ATTEMPTS = 3
MAX_RETRIES_PER_TASK = 3

# Backoff configuration
BACKOFF_BASE_DELAY = 1.0  # seconds
BACKOFF_MAX_DELAY = 30.0  # seconds

# Context management thresholds
TOKEN_THRESHOLD = 50000   # chars / 4 ~ tokens for compact trigger
CONTEXT_COMPRESS_THRESHOLD = 80000  # chars

# Recovery messages
CONTINUATION_MESSAGE = (
    "Output limit hit. Continue directly from where you stopped -- "
    "no recap, no repetition. Pick up mid-sentence if needed."
)

# Tool result placeholders
TOOL_RESULT_PLACEHOLDER = "[Earlier tool result compacted. Re-run the tool if you need full detail.]"
MICRO_COMPACT_THRESHOLD = 120  # chars > this threshold get compacted
