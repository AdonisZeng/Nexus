"""Scan directories and parse SKILL.md files."""
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from logging import getLogger

from .scope import SkillScope, get_skill_roots, get_user_skills_dir
from src.utils.frontmatter import parse_frontmatter

logger = getLogger(__name__)


@dataclass
class SkillMetadata:
    """Lightweight skill metadata without handler"""
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    license: Optional[str] = None
    requires_args: bool = True
    docstring: str = ""
    file_path: Optional[Path] = None


class SKILLParser:
    """Parse SKILL.md files with YAML frontmatter"""

    @classmethod
    def parse(cls, file_path: Path) -> tuple[dict, str]:
        """Parse a SKILL.md file. Returns (frontmatter_dict, content_str)"""
        return parse_frontmatter(file_path.read_text(encoding="utf-8"))

    @classmethod
    def to_skill_metadata(cls, file_path: Path) -> SkillMetadata:
        """Convert SKILL.md file to SkillMetadata"""
        frontmatter, docstring = cls.parse(file_path)

        return SkillMetadata(
            name=frontmatter.get("name", file_path.parent.name),
            description=frontmatter.get("description", ""),
            triggers=frontmatter.get("trigger", frontmatter.get("triggers", [])),
            aliases=frontmatter.get("aliases", []),
            license=frontmatter.get("license"),
            requires_args=frontmatter.get("requires_args", True),
            docstring=docstring,
            file_path=file_path,
        )


class SkillLoader:
    """Scan directories and load skills (pure Markdown, no Python handlers)"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or Path(__file__).parent

    def scan_directory(self, directory: Path) -> list[Path]:
        """Scan a directory for skill subdirectories with SKILL.md"""
        skill_paths = []

        if not directory.exists():
            return skill_paths

        for item in directory.iterdir():
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    skill_paths.append(skill_md)

        return skill_paths

    def load_skill(self, skill_md_path: Path) -> Optional[SkillMetadata]:
        """Load a single skill's metadata from its SKILL.md file"""
        try:
            return SKILLParser.to_skill_metadata(skill_md_path)
        except Exception as e:
            logger.error(f"Failed to load skill from {skill_md_path}: {e}")
            return None

    def load_all(self) -> list[SkillMetadata]:
        """Load all skills' metadata from configured directories"""
        skills = []
        seen_names = set()

        for scope, directory in self._get_skill_directories():
            if not directory.exists():
                continue

            for skill_md in self.scan_directory(directory):
                skill = self.load_skill(skill_md)
                if skill and skill.name not in seen_names:
                    skills.append(skill)
                    seen_names.add(skill.name)

        return skills

    def _get_skill_directories(self) -> list[tuple[SkillScope, Path]]:
        """Get all skill source directories from configured scopes"""
        roots = get_skill_roots()
        result = []
        for scope, path in roots:
            if scope == SkillScope.SYSTEM:
                continue  # system scope is internal, not exposed to users
            result.append((scope, path))
        return result


class SkillCatalog:
    """Two-layer skill model: cheap catalog + on-demand full body (pure Markdown)"""

    _MAX_BODY_CACHE = 50  # LRU cache max size

    def __init__(self):
        self._metadata_cache: Optional[dict[str, SkillMetadata]] = None

    def describe_available(self) -> str:
        """Return cheap catalog (name: description) for system prompt"""
        self._ensure_metadata()
        if not self._metadata_cache:
            return "(no skills available)"
        lines = []
        for name in sorted(self._metadata_cache):
            meta = self._metadata_cache[name]
            triggers = ", ".join(meta.triggers[:3]) if meta.triggers else ""
            triggers_str = f" (触发词: {triggers})" if triggers else ""
            lines.append(f"- {name}: {meta.description}{triggers_str}")
        return "\n".join(lines)

    def load_full_text(self, name: str) -> str:
        """Load full skill body on-demand with LRU cache (max 50 entries)."""
        self._ensure_metadata()
        if name not in self._metadata_cache:
            known = ", ".join(sorted(self._metadata_cache)) or "(none)"
            return f"Error: Unknown skill '{name}'. Available: {known}"

        meta = self._metadata_cache[name]
        if not meta.file_path or not meta.file_path.exists():
            return f"Error: Skill file not found for '{name}'"

        return self._load_body_cached(meta.name, str(meta.file_path))

    @lru_cache(maxsize=50)
    def _load_body_cached(self, name: str, file_path_str: str) -> str:
        """Cached skill body loader. Key by (name, file_path_str) for cache isolation."""
        meta = self._metadata_cache[name]
        _, body = SKILLParser.parse(Path(file_path_str))
        return f'<skill name="{meta.name}">\n{meta.description}\n{body}\n</skill>'

    def _ensure_metadata(self) -> None:
        """Ensure metadata cache is populated"""
        if self._metadata_cache is None:
            self._metadata_cache = {}
            loader = SkillLoader()
            for meta in loader.load_all():
                self._metadata_cache[meta.name] = meta

    def invalidate_cache(self, name: Optional[str] = None) -> None:
        """Clear caches. Pass name=None to clear all."""
        self._load_body_cached.cache_clear()
        if name:
            self._metadata_cache.pop(name, None)
        else:
            self._metadata_cache = None


__all__ = [
    "SkillMetadata",
    "SKILLParser",
    "SkillLoader",
    "SkillCatalog",
]
