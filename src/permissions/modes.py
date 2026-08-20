"""Permission mode definitions"""
from enum import Enum


class PermissionMode(Enum):
    """Permission modes for controlling tool execution.

    - NORMAL: Allow all tools
    - READ_ONLY: Block mutating tools
    - ASK: (Future) Prompt user for each mutating tool
    """

    NORMAL = "normal"
    READ_ONLY = "read_only"
    ASK = "ask"

    @classmethod
    def from_string(cls, value: str) -> "PermissionMode":
        """Parse a string into a PermissionMode.

        Args:
            value: String representation of mode

        Returns:
            PermissionMode enum value

        Raises:
            ValueError: If value is not a valid mode
        """
        try:
            return cls(value)
        except ValueError:
            valid = [m.value for m in cls]
            raise ValueError(f"Unknown mode: {value}. Choose from {valid}")
