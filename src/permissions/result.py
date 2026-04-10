"""Permission result dataclass"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PermissionResult:
    """Result of a permission check."""

    allowed: bool
    reason: Optional[str] = None
    mode_applied: str = "normal"
    needs_confirmation: bool = False  # ASK mode requires user interaction

    def __bool__(self) -> bool:
        return self.allowed
