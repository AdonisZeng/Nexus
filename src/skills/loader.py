"""Skill loader - scan directories and parse SKILL.md files"""

import re
import yaml
import importlib.util
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, AsyncIterator
from logging import getLogger

logger = getLogger(__name__)


def get_user_skills_dir() -> Path:
    """Get the user skills directory (~/.nexus/skills)"""
    user_dir = Path(os.path.expanduser("~"))
    nexus_skills = user_dir / ".nexus" / "skills"
    # Auto-create if not exists
    nexus_skills.mkdir(parents=True, exist_ok=True)
    return nexus_skills


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


@dataclass
class LoadedSkill(SkillMetadata):
    """Fully loaded skill with handler"""
    handler: Optional[Callable[..., AsyncIterator[Any]]] = None


class SKILLParser:
    """Parse SKILL.md files with YAML frontmatter"""

    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n(.*)$",
        re.DOTALL | re.MULTILINE
    )

    @classmethod
    def parse(cls, file_path: Path) -> tuple[dict, str]:
        """
        Parse a SKILL.md file.
        Returns (frontmatter_dict, content_str)
        """
        content = file_path.read_text(encoding="utf-8")

        match = cls.FRONTMATTER_PATTERN.match(content)
        if match:
            frontmatter_str = match.group(1)
            docstring = match.group(2).strip()
            try:
                frontmatter = yaml.safe_load(frontmatter_str) or {}
                return frontmatter, docstring
            except yaml.YAMLError as e:
                logger.warning(f"Failed to parse YAML in {file_path}: {e}")
                return {}, content.strip()

        return {}, content.strip()

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


class ModuleLoader:
    """Dynamically load Python modules from skill directories"""

    @classmethod
    def load_module(cls, directory: Path, module_name: str = "skill"):
        """
        Load a Python module from a directory.
        Looks for __init__.py or {module_name}.py
        """
        # Try __init__.py first
        init_file = directory / "__init__.py"
        if init_file.exists():
            return cls._load_file(init_file)

        # Try module_name.py
        module_file = directory / f"{module_name}.py"
        if module_file.exists():
            return cls._load_file(module_file)

        # Try any .py file
        py_files = list(directory.glob("*.py"))
        if py_files:
            return cls._load_file(py_files[0])

        return None

    @classmethod
    def _load_file(cls, file_path: Path):
        """Load a specific Python file"""
        module_name = f"skill.{file_path.parent.name}.{file_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        return None


class SkillLoader:
    """Scan directories and load skills"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or Path(__file__).parent
        self.parser = SKILLParser()
        self.module_loader = ModuleLoader()

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

    def load_skill(self, skill_md_path: Path) -> Optional[LoadedSkill]:
        """Load a single skill from its SKILL.md file"""
        try:
            metadata = self.parser.to_skill_metadata(skill_md_path)
            skill_dir = skill_md_path.parent

            # Load the Python module
            module = self.module_loader.load_module(skill_dir)
            handler = None

            if module:
                # Look for skill handler
                # Try {name}_handler first
                handler_name = f"{metadata.name}_handler"
                if hasattr(module, handler_name):
                    handler = getattr(module, handler_name)
                # Try handler attribute
                elif hasattr(module, "handler"):
                    handler = getattr(module, "handler")
                # Try {name}_skill and get its handler
                skill_obj_name = f"{metadata.name}_skill"
                if hasattr(module, skill_obj_name):
                    skill_obj = getattr(module, skill_obj_name)
                    if hasattr(skill_obj, "handler"):
                        handler = skill_obj.handler

            return LoadedSkill(
                name=metadata.name,
                description=metadata.description,
                triggers=metadata.triggers,
                aliases=metadata.aliases,
                license=metadata.license,
                requires_args=metadata.requires_args,
                docstring=metadata.docstring,
                file_path=metadata.file_path,
                handler=handler,
            )

        except Exception as e:
            logger.error(f"Failed to load skill from {skill_md_path}: {e}")
            return None

    def load_all(self, directories: Optional[list[Path]] = None) -> list[LoadedSkill]:
        """Load all skills from given directories"""
        if directories is None:
            directories = [
                self.skills_dir / "builtin",
                self.skills_dir / "custom",
                get_user_skills_dir(),  # ~/.nexus/skills
            ]

        skills = []
        seen_names = set()

        for directory in directories:
            if not directory.exists():
                continue

            for skill_md in self.scan_directory(directory):
                skill = self.load_skill(skill_md)
                if skill and skill.name not in seen_names:
                    skills.append(skill)
                    seen_names.add(skill.name)

        return skills

    def discover_skills(self, base_dir: Optional[Path] = None) -> list[SkillMetadata]:
        """Discover all skills without loading their handlers"""
        base_dir = base_dir or self.skills_dir
        metadata_list = []

        directories = [
            base_dir / "builtin",
            base_dir / "custom",
            get_user_skills_dir(),  # ~/.nexus/skills
        ]

        for directory in directories:
            if not directory.exists():
                continue

            for skill_md in self.scan_directory(directory):
                try:
                    metadata = self.parser.to_skill_metadata(skill_md)
                    metadata_list.append(metadata)
                except Exception as e:
                    logger.warning(f"Failed to parse {skill_md}: {e}")

        return metadata_list


__all__ = [
    "SkillMetadata",
    "LoadedSkill",
    "SKILLParser",
    "SkillLoader",
]