"""Skill system - slash commands like Claude Code"""
from dataclasses import dataclass, field
from typing import Callable, Awaitable, AsyncIterator, Any
import re


@dataclass
class Skill:
    """Skill definition - like a slash command"""
    name: str                           # Command name (e.g., "commit")
    description: str                    # Description for help
    aliases: list = field(default_factory=list)  # Aliases (e.g., ["ci"])
    handler: Callable = None            # Async handler function
    requires_args: bool = True          # Whether args are required

    def __post_init__(self):
        if self.aliases is None:
            self.aliases = []


class SkillRegistry:
    """Registry for skills (slash commands)"""

    def __init__(self):
        self.skills: dict[str, Skill] = {}

    def register(self, skill: Skill):
        """Register a skill"""
        self.skills[skill.name] = skill
        # Register aliases
        for alias in skill.aliases:
            self.skills[alias] = skill

    def get(self, name: str) -> Skill:
        """Get a skill by name"""
        return self.skills.get(name)

    def list_skills(self) -> list[str]:
        """List all skill names"""
        # Remove aliases from list
        seen = set()
        result = []
        for name in self.skills.keys():
            skill = self.skills[name]
            if skill.name not in seen:
                result.append(skill.name)
                seen.add(skill.name)
        return result

    def get_help(self) -> str:
        """Get help text for all skills"""
        lines = ["Available commands (/):"]
        for name in self.list_skills():
            skill = self.skills[name]
            lines.append(f"  /{name} - {skill.description}")
        return "\n".join(lines)

    def parse_input(self, user_input: str) -> tuple[str, str, str] | None:
        """
        Parse user input to detect slash command.
        Returns (skill_name, args, remaining_input) or None if not a command.
        """
        user_input = user_input.strip()
        if not user_input.startswith("/"):
            return None

        # Parse /command args
        match = re.match(r"/(\w+)(?:\s+(.*))?", user_input)
        if match:
            return match.group(1), match.group(2) or "", ""

        return None


# Re-export for convenience
__all__ = ["Skill", "SkillRegistry"]