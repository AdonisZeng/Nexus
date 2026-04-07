"""
NEXUS.md Loader - Static project knowledge loaded at session start

Supports two levels (priority order):
1. Global:  ~/.nexus/NEXUS.md     (user-wide knowledge)
2. Project: ./NEXUS.md           (project-specific knowledge)

File format:
---
version: 1.0
scope: project
priority: 10
---

# Project Knowledge
...
"""

import re
import os
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


FRONTMATTER_PATTERN = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL | re.MULTILINE
)


@dataclass
class NexusMDMetadata:
    version: str = "1.0"
    scope: str = "project"
    priority: int = 0


@dataclass
class NexusMD:
    metadata: NexusMDMetadata
    content: str
    source_path: Optional[Path] = None


class NexusMDLoader:
    """Load and merge NEXUS.md files from multiple levels."""

    @staticmethod
    def get_global_path() -> Path:
        """Get global NEXUS.md path: ~/.nexus/NEXUS.md"""
        user_home = Path(os.path.expanduser("~"))
        return user_home / ".nexus" / "NEXUS.md"

    @staticmethod
    def get_project_path(cwd: Optional[Path] = None) -> Optional[Path]:
        """Get project NEXUS.md path: ./NEXUS.md (relative to cwd)"""
        if cwd is None:
            cwd = Path.cwd()
        project_path = cwd / "NEXUS.md"
        return project_path if project_path.exists() else None

    @classmethod
    def parse(cls, file_path: Path) -> Optional[NexusMD]:
        """Parse a single NEXUS.md file."""
        if not file_path.exists():
            return None

        content = file_path.read_text(encoding="utf-8")
        if not content.strip():
            return None

        match = FRONTMATTER_PATTERN.match(content)
        if match:
            frontmatter_str = match.group(1)
            frontmatter = yaml.safe_load(frontmatter_str) or {}
            metadata = NexusMDMetadata(
                version=str(frontmatter.get("version", "1.0")),
                scope=frontmatter.get("scope", "project"),
                priority=int(frontmatter.get("priority", 0)),
            )
            docstring = match.group(2).strip()
        else:
            # No frontmatter, treat entire content as docstring
            metadata = NexusMDMetadata()
            docstring = content.strip()

        return NexusMD(metadata=metadata, content=docstring, source_path=file_path)

    @classmethod
    def load_all(cls, cwd: Optional[Path] = None) -> list[NexusMD]:
        """Load all available NEXUS.md files, sorted by priority."""
        results = []

        # Load global first
        global_md = cls.parse(cls.get_global_path())
        if global_md:
            results.append(global_md)

        # Load project-level
        project_md = cls.parse(cls.get_project_path(cwd))
        if project_md:
            results.append(project_md)

        # Sort by priority (higher first)
        results.sort(key=lambda x: x.metadata.priority, reverse=True)
        return results

    @classmethod
    def merge(cls, nexus_list: list[NexusMD]) -> str:
        """Merge multiple NEXUS.md into a single string."""
        if not nexus_list:
            return ""

        parts = []
        for i, nexus in enumerate(nexus_list):
            if nexus.source_path:
                label = "全局知识" if "global" in str(nexus.source_path) else "项目知识"
                parts.append(f"\n## {label}\n")
            parts.append(nexus.content)
            if i < len(nexus_list) - 1:
                parts.append("\n---\n")

        return "\n".join(parts)

    @classmethod
    def load_and_merge(cls, cwd: Optional[Path] = None) -> str:
        """Load all NEXUS.md files and merge into a single string."""
        return cls.merge(cls.load_all(cwd))

    @classmethod
    def exists(cls, cwd: Optional[Path] = None) -> bool:
        """Check if any NEXUS.md exists."""
        return cls.get_global_path().exists() or cls.get_project_path(cwd) is not None


__all__ = ["NexusMDLoader", "NexusMD", "NexusMDMetadata"]
