"""Command registry for managing built-in commands"""
from typing import Optional
import logging

from .base import Command, CommandContext

logger = logging.getLogger(__name__)


class CommandRegistry:
    """Registry for all built-in commands"""

    def __init__(self):
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, str] = {}

    def register(self, command: Command) -> None:
        """
        Register a command.

        Args:
            command: Command instance to register
        """
        if not command.name:
            raise ValueError(f"Command must have a name: {command}")

        if command.name in self._commands:
            logger.warning(f"Command {command.name} already registered, replacing")

        self._commands[command.name.lower()] = command

        for alias in command.aliases:
            self._aliases[alias.lower()] = command.name.lower()

        logger.debug(f"Registered command: {command.name}")

    def _register_builtin_commands(self) -> None:
        """Register all built-in commands"""
        from .builtin import (
            help_command,
            plan_command,
            sessions_command,
            restore_command,
            mcpstatus_command,
            settings_command,
            models_command,
            reload_command,
            clear_command,
            exit_command,
            agents_command,
            tasks_command,
            teams_command,
        )
        self.register(help_command)
        self.register(plan_command)
        self.register(sessions_command)
        self.register(restore_command)
        self.register(mcpstatus_command)
        self.register(settings_command)
        self.register(models_command)
        self.register(reload_command)
        self.register(clear_command)
        self.register(exit_command)
        self.register(agents_command)
        self.register(tasks_command)
        self.register(teams_command)

    def get(self, name: str) -> Optional[Command]:
        """
        Get a command by name or alias.

        Args:
            name: Command name or alias

        Returns:
            Command instance or None if not found
        """
        name_lower = name.lower()

        if name_lower in self._commands:
            return self._commands[name_lower]

        if name_lower in self._aliases:
            return self._commands[self._aliases[name_lower]]

        return None

    def list_commands(self) -> list[str]:
        """
        List all registered command names.

        Returns:
            List of command names
        """
        return list(self._commands.keys())

    def get_all(self) -> list[Command]:
        """
        Get all registered commands.

        Returns:
            List of all Command instances
        """
        return list(self._commands.values())

    def parse_input(self, command_input: str) -> tuple[Optional[str], Optional[str], str]:
        """
        Parse command input into name, command, and args.

        Args:
            command_input: Raw command input like "/commit message" or "/commit"

        Returns:
            Tuple of (command_name, command_instance, args)
            command_name is None if not a command
        """
        if not command_input.strip().startswith('/'):
            return None, None, command_input

        parts = command_input.strip().split(None, 1)
        cmd_part = parts[0].lstrip('/').lower()
        args = parts[1] if len(parts) > 1 else ""

        command = self.get(cmd_part)
        if command:
            return cmd_part, command, args

        return cmd_part, None, args

    def get_help_text(self) -> str:
        """
        Generate help text from all commands.

        Returns:
            Formatted help text
        """
        lines = ["可用命令："]
        for cmd in self.get_all():
            cmd_names = [cmd.name] + cmd.aliases
            lines.append(f"  /{'  /'.join(cmd_names)} - {cmd.description}")
        return "\n".join(lines)


_global_registry: Optional[CommandRegistry] = None


def get_command_registry() -> CommandRegistry:
    """Get the global command registry instance"""
    global _global_registry
    if _global_registry is None:
        _global_registry = CommandRegistry()
        _global_registry._register_builtin_commands()
    return _global_registry


__all__ = [
    "CommandRegistry",
    "Command",
    "CommandContext",
    "get_command_registry",
]
