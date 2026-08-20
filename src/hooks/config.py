"""Hook configuration loading from global config file."""
import json
import os
from pathlib import Path
from typing import Optional

from .models import HookDefinition


_HOOKS_CONFIG_FILE = Path.home() / ".nexus" / "hooks.json"


def load_hooks_config(config_path: Optional[Path] = None) -> dict:
    """
    Load hooks configuration from JSON file.

    Args:
        config_path: Path to hooks.json. Defaults to ~/.nexus/hooks.json

    Returns:
        Dict with 'hooks' key mapping event names to hook lists
    """
    path = config_path or _HOOKS_CONFIG_FILE
    if not path.exists():
        return {"hooks": {}}

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config if isinstance(config, dict) else {"hooks": {}}
    except (json.JSONDecodeError, IOError):
        return {"hooks": {}}


def get_hooks_for_event(
    config: dict,
    event: str
) -> list[HookDefinition]:
    """
    Extract hook definitions for a specific event from config.

    Args:
        config: Loaded hooks config dict
        event: Event name (e.g., "iteration_start")

    Returns:
        List of HookDefinition objects
    """
    hooks_list = config.get("hooks", {}).get(event, [])
    if not isinstance(hooks_list, list):
        return []
    return [HookDefinition.from_dict(h) for h in hooks_list if isinstance(h, dict)]


def is_trust_all_enabled(config: Optional[dict] = None) -> bool:
    """Check if trust_all is enabled in config."""
    if config is None:
        config = load_hooks_config()
    return config.get("trust_all", False) is True


__all__ = [
    "load_hooks_config",
    "get_hooks_for_event",
    "is_trust_all_enabled",
    "_HOOKS_CONFIG_FILE",
]
