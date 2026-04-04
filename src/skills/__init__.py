"""Skills module - True skill system for Nexus"""
from .registry import Skill, SkillRegistry
from .loader import SkillLoader, SkillMetadata, get_user_skills_dir
from .scope import SkillScope, get_skill_roots, get_repo_skills_dir, get_system_skills_dir

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillLoader",
    "SkillMetadata",
    "get_user_skills_dir",
    "SkillScope",
    "get_skill_roots",
    "get_repo_skills_dir",
    "get_system_skills_dir",
]


def load_all_skills_metadata() -> list[SkillMetadata]:
    """加载所有可用 skills 的元数据（自定义 + 用户目录）"""
    loader = SkillLoader()
    return loader.discover_skills()