"""Built-in commands (grouped by domain)"""
from .agents import agents_command
from .session_cmds import (
    sessions_command,
    restore_command,
    clear_command,
    exit_command,
    reload_command,
    ClearCommand,
    ExitCommand,
    ReloadCommand,
)
from .mode_cmds import (
    plan_command,
    tasks_command,
    models_command,
    settings_command,
    prompt_command,
    ModelsCommand,
    SettingsCommand,
    PromptCommand,
)
from .info_cmds import (
    help_command,
    mcpstatus_command,
    teams_command,
    HelpCommand,
    McpStatusCommand,
    TeamsCommand,
)

__all__ = [
    "agents_command",
    "clear_command",
    "ClearCommand",
    "exit_command",
    "ExitCommand",
    "help_command",
    "HelpCommand",
    "mcpstatus_command",
    "McpStatusCommand",
    "models_command",
    "ModelsCommand",
    "plan_command",
    "reload_command",
    "ReloadCommand",
    "restore_command",
    "sessions_command",
    "settings_command",
    "SettingsCommand",
    "tasks_command",
    "teams_command",
    "TeamsCommand",
    "prompt_command",
    "PromptCommand",
]
