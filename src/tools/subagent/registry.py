"""Subagent registry - loads and manages subagent configurations"""
import asyncio
import os
from pathlib import Path
from typing import Optional

from src.utils import get_logger

from .models import SubagentConfig
from .parser import SubagentParser

logger = get_logger("subagent.registry")


class SubagentRegistry:
    """Registry for managing subagent configurations"""

    DEFAULT_AGENTS_DIR = Path.home() / ".nexus" / "agents"

    def __init__(self):
        self.agents: dict[str, SubagentConfig] = {}
        self._load_lock = asyncio.Lock()

    def load_agents(self, directory: Optional[Path] = None) -> dict[str, SubagentConfig]:
        """
        Load all subagent configurations from .md files in the directory.
        Creates the directory if it doesn't exist.
        """
        directory = directory or self.DEFAULT_AGENTS_DIR
        directory.mkdir(parents=True, exist_ok=True)

        loaded = {}
        for md_file in directory.glob("*.md"):
            try:
                config = SubagentParser.to_config(md_file)
                loaded[config.name] = config
                logger.debug(f"Loaded subagent: {config.name} from {md_file}")
            except Exception as e:
                logger.warning(f"Failed to load subagent from {md_file}: {e}")

        self.agents = loaded
        logger.info(f"Loaded {len(self.agents)} subagent(s) from {directory}")
        return loaded

    def get(self, name: str) -> Optional[SubagentConfig]:
        """Get a subagent by name"""
        return self.agents.get(name)

    def find_by_description(self, query: str) -> Optional[SubagentConfig]:
        """
        Find a subagent by matching the query against descriptions.
        Uses simple keyword matching for now.
        """
        if not query:
            return None

        query_lower = query.lower()
        best_match = None
        best_score = 0

        for agent in self.agents.values():
            desc_lower = agent.description.lower()
            # Check if query keywords appear in description
            if query_lower in desc_lower:
                # Score based on position - earlier matches are better
                pos = desc_lower.find(query_lower)
                score = 1000 - pos
                if score > best_score:
                    best_score = score
                    best_match = agent

        return best_match

    def list_agents(self) -> list[str]:
        """List all available subagent names"""
        return list(self.agents.keys())

    def reload(self) -> None:
        """Reload all subagent configurations"""
        self.load_agents()


__all__ = ["SubagentRegistry"]
