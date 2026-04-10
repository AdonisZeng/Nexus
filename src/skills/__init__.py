"""Skills module - Two-layer skill system for Nexus"""
from typing import Optional

from .registry import Skill, SkillRegistry
from .loader import SkillMetadata, SkillCatalog, get_user_skills_dir
from .scope import SkillScope, get_skill_roots, get_repo_skills_dir, get_system_skills_dir

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillMetadata",
    "SkillCatalog",
    "get_user_skills_dir",
    "SkillScope",
    "get_skill_roots",
    "get_repo_skills_dir",
    "get_system_skills_dir",
]


# Global singleton for the two-layer skill model
_skill_catalog: Optional[SkillCatalog] = None


def get_skill_catalog() -> SkillCatalog:
    """Get the global SkillCatalog singleton (two-layer model)"""
    global _skill_catalog
    if _skill_catalog is None:
        _skill_catalog = SkillCatalog()
    return _skill_catalog
