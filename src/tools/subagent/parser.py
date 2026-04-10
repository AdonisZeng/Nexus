"""Subagent configuration file parser"""
from pathlib import Path
from typing import Tuple

from .models import SubagentConfig
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
        allowed_tools = frontmatter.get("allowed-tools", [])
        if isinstance(allowed_tools, str):
            allowed_tools = [t.strip() for t in allowed_tools.split("\n") if t.strip()]

        # Parse denied-tools
        denied_tools = frontmatter.get("denied-tools", [])
        if isinstance(denied_tools, str):
            denied_tools = [t.strip() for t in denied_tools.split("\n") if t.strip()]

        # Parse max-iterations
        max_iterations = frontmatter.get("max-iterations", 10)
        if isinstance(max_iterations, str):
            max_iterations = int(max_iterations)

        # Parse timeout-seconds
        timeout_seconds = frontmatter.get("timeout-seconds", 300.0)
        if isinstance(timeout_seconds, str):
            timeout_seconds = float(timeout_seconds)

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
        )


__all__ = ["SubagentParser", "SubagentConfig", "SubagentResult"]
