"""Subagent configuration file parser"""
from pathlib import Path
from typing import Tuple, Optional

from .models import SubagentConfig, HookDefinition
from src.utils.frontmatter import parse_frontmatter


class SubagentParser:
    """Parse subagent .md configuration files with YAML frontmatter"""

    @classmethod
    def parse(cls, file_path: Path) -> Tuple[dict, str]:
        """Parse a subagent .md file. Returns (frontmatter_dict, system_prompt_content)"""
        return parse_frontmatter(file_path.read_text(encoding="utf-8"))

    @classmethod
    def to_config(cls, file_path: Path) -> SubagentConfig:
        """Convert a .md file to SubagentConfig"""
        frontmatter, system_prompt = cls.parse(file_path)

        name = frontmatter.get("name")
        if not name:
            # Use filename (without .md) as name
            name = file_path.stem

        # Parse allowed-tools (can be list or newline-separated string)
        allowed_tools = cls._parse_list_field(frontmatter.get("allowed-tools"))

        # Parse denied-tools
        denied_tools = cls._parse_list_field(frontmatter.get("denied-tools"))

        # Parse max-iterations
        max_iterations = cls._parse_int(frontmatter.get("max-iterations"), 10)

        # Parse timeout-seconds
        timeout_seconds = cls._parse_float(frontmatter.get("timeout-seconds"), 300.0)

        # Parse cwd
        cwd = frontmatter.get("cwd")

        # Parse env (KEY=VALUE format, can be dict or multiline string)
        env = cls._parse_env(frontmatter.get("env"))

        # Parse hooks
        hooks = cls._parse_hooks(frontmatter.get("hooks"))

        # Parse skills
        skills = cls._parse_list_field(frontmatter.get("skills"))

        # Parse permission_mode
        permission_mode = frontmatter.get("permission-mode", "normal")
        if permission_mode not in ("normal", "read_only"):
            permission_mode = "normal"

        # Parse tool-parameters
        tool_parameters = frontmatter.get("tool-parameters", {})

        # Parse background
        background = bool(frontmatter.get("background", False))

        return SubagentConfig(
            name=name,
            description=frontmatter.get("description", ""),
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            model=frontmatter.get("model"),
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            file_path=file_path,
            cwd=cwd,
            env=env,
            hooks=hooks,
            skills=skills,
            permission_mode=permission_mode,
            tool_parameters=tool_parameters,
            background=background,
        )

    @classmethod
    def _parse_list_field(cls, value) -> list[str]:
        """Parse a list field (can be list or newline-separated string)"""
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if v]
        if isinstance(value, str):
            return [t.strip() for t in value.split("\n") if t.strip()]
        return []

    @classmethod
    def _parse_int(cls, value, default: int) -> int:
        """Parse integer with fallback to default"""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def _parse_float(cls, value, default: float) -> float:
        """Parse float with fallback to default"""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def _parse_env(cls, value) -> Optional[dict[str, str]]:
        """Parse env field (can be dict or multiline KEY=VALUE string)"""
        if not value:
            return None
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        if isinstance(value, str):
            env_dict = {}
            for line in value.split("\n"):
                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()
                    if key:
                        env_dict[key] = val
            return env_dict if env_dict else None
        return None

    @classmethod
    def _parse_hooks(cls, value) -> Optional[dict[str, list[HookDefinition]]]:
        """Parse hooks field from frontmatter"""
        if not value:
            return None
        if not isinstance(value, dict):
            return None

        hooks = {}
        for event, hook_list in value.items():
            if not isinstance(hook_list, list):
                continue
            parsed_hooks = []
            for hook_def in hook_list:
                if isinstance(hook_def, dict):
                    command = hook_def.get("command", "")
                    if command:
                        parsed_hooks.append(HookDefinition(
                            command=command,
                            matcher=hook_def.get("matcher"),
                        ))
            if parsed_hooks:
                hooks[event] = parsed_hooks

        return hooks if hooks else None


__all__ = ["SubagentParser", "SubagentConfig", "SubagentResult", "HookDefinition"]
