"""Command completer - using prompt_toolkit for interactive completion"""
import asyncio
from typing import Optional, List

# prompt_toolkit for interactive completion
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

# Built-in commands - loaded from command registry
def get_builtin_commands() -> List[str]:
    """Get built-in commands from command registry (main commands only, no aliases)"""
    try:
        from src.commands import get_command_registry
        registry = get_command_registry()
        commands = []
        for cmd in registry.get_all():
            commands.append(f"/{cmd.name}")
        return commands
    except Exception:
        # Fallback to basic commands if registry fails
        return ["/help", "/exit", "/quit", "/clear", "/sessions", "/restore", "/models", "/settings", "/reload", "/mcpstatus", "/plan", "/search", "/docs", "/test"]


class CommandCompleter(Completer):
    """Command completer using prompt_toolkit's Completer interface"""

    def __init__(self, skill_commands: Optional[List[str]] = None):
        all_commands = get_builtin_commands()
        if skill_commands:
            all_commands.extend(skill_commands)
        # Remove duplicates while preserving order
        seen = set()
        self.commands = []
        for cmd in all_commands:
            if cmd not in seen:
                seen.add(cmd)
                self.commands.append(cmd)

    def get_completions(self, document, complete_event):
        """Generate completions based on current input"""
        text = document.text_before_cursor

        # Only provide completions for commands starting with /
        if not text.startswith("/"):
            return

        # Find matching commands
        for cmd in self.commands:
            if cmd.lower().startswith(text.lower()):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd
                )


# Custom key bindings for Ctrl+C handling
def create_key_bindings():
    """Create key bindings for the prompt"""
    kb = KeyBindings()

    @kb.add(Keys.ControlC, eager=True)
    def _(event):
        # Raise KeyboardInterrupt on Ctrl+C
        raise KeyboardInterrupt()

    return kb


# Custom style matching Rich UI colors
def create_style():
    """Create prompt_toolkit style"""
    return Style.from_dict({
        'completer': 'ansicyan',
        'completion-menu': 'bg:ansiblack ansiwhite',
        'completion-menu.completion': 'ansiblack ansiwhite',
        'completion-menu.completion.current': 'ansiblack ansicyan',
        'completion-toolbar': 'bg:ansiblack ansiwhite',
    })


def get_input_with_completion(
    prompt_str: str,
    completer: CommandCompleter,
    default: str = ""
) -> str:
    """
    Get user input with interactive completion using prompt_toolkit.

    Args:
        prompt_str: The prompt to display
        completer: The CommandCompleter instance
        default: Default text (optional)

    Returns:
        User input string
    """
    try:
        user_input = prompt(
            prompt_str,
            completer=completer,
            default=default,
            key_bindings=create_key_bindings(),
            style=create_style(),
            # Enable mouse support for completion menu
            mouse_support=True,
            # Don't auto-select first completion
            complete_while_typing=True,
        )
        return user_input.strip()
    except KeyboardInterrupt:
        # Re-raise to be handled by caller
        raise KeyboardInterrupt()
    except EOFError:
        return ""


async def get_input_async(prompt_str: str, completer: Optional[CommandCompleter] = None) -> str:
    """
    Async wrapper for get_input_with_completion.
    Runs the synchronous prompt in an executor to not block the event loop.
    """
    if completer is None:
        # Fallback to simple input if no completer
        return input(prompt_str)

    loop = asyncio.get_event_loop()
    try:
        user_input = await loop.run_in_executor(
            None, get_input_with_completion, prompt_str, completer, ""
        )
        return user_input
    except KeyboardInterrupt:
        raise asyncio.CancelledError()


def get_input(prompt_str: str, completer: Optional[CommandCompleter] = None) -> str:
    """Synchronous input with completion (for backwards compatibility)"""
    if completer is None:
        return input(prompt_str)

    try:
        return get_input_with_completion(prompt_str, completer, "")
    except KeyboardInterrupt:
        return ""


# Legacy functions for backwards compatibility
def create_input_session(skill_commands: Optional[List[str]] = None) -> CommandCompleter:
    """Create a command completer session"""
    return CommandCompleter(skill_commands)


def update_completions(completer: CommandCompleter, skill_commands: List[str]) -> None:
    """Update completions (legacy function)"""
    # This is now handled by creating a new CommandCompleter
    pass


def request_exit():
    """Legacy function - not needed with prompt_toolkit"""
    pass


def suggest_completion(user_input: str, commands: List[str]) -> Optional[str]:
    """Legacy function for simple completion suggestion"""
    if not user_input.startswith("/"):
        return None

    matches = [cmd for cmd in commands if cmd.lower().startswith(user_input.lower())]
    if len(matches) == 1:
        return matches[0]
    return None