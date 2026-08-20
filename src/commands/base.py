"""Command base classes and data structures"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, Any
from enum import Enum


class CommandResultType(Enum):
    """Command result types"""
    OUTPUT = "output"
    THINKING = "thinking"
    ERROR = "error"
    WARNING = "warning"
    SUCCESS = "success"


@dataclass
class CommandResult:
    """Result from a command execution"""
    type: CommandResultType
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class CommandContext:
    """Context passed to commands when executing"""
    args: str = ""
    cli: Any = None
    session_id: str = ""
    session: dict = field(default_factory=dict)


class Command(ABC):
    """Base class for all commands"""

    name: str = ""
    description: str = ""
    aliases: list[str] = []
    requires_context: bool = False

    @abstractmethod
    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """
        Execute the command.

        Args:
            context: Command context with arguments and app/cli reference

        Yields:
            CommandResult objects containing output
        """
        pass

    def can_handle(self, command_input: str) -> bool:
        """
        Check if this command can handle the given input.

        Args:
            command_input: The raw command input (e.g., "/commit args")

        Returns:
            True if this command handles the input
        """
        command_input = command_input.strip().lstrip('/')
        parts = command_input.split(None, 1)
        cmd_name = parts[0].lower()

        return (
            cmd_name == self.name.lower() or
            cmd_name in [a.lower() for a in self.aliases]
        )

    def parse_args(self, command_input: str) -> str:
        """
        Parse arguments from command input.

        Args:
            command_input: The raw command input

        Returns:
            The arguments string
        """
        command_input = command_input.strip().lstrip('/')
        parts = command_input.split(None, 1)
        return parts[1] if len(parts) > 1 else ""


__all__ = [
    "Command",
    "CommandContext",
    "CommandResult",
    "CommandResultType",
]
