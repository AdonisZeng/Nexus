"""Skill 作用域管理

支持多级作用域：
- REPO: 项目级 (.nexus/skills/)
- USER: 用户级 (~/.nexus/skills/)
- SYSTEM: 系统级 (~/.nexus/skills/.system/)
"""

import logging
from enum import Enum
from pathlib import Path
from typing import Optional
import os

logger = logging.getLogger("Nexus")


class SkillScope(Enum):
    """Skill 作用域"""
    REPO = "repo"      # 项目级：.nexus/skills/
    USER = "user"      # 用户级：~/.nexus/skills/
    SYSTEM = "system"  # 系统级：~/.nexus/skills/.system/


# 作用域优先级（数字越大优先级越高，同名 skill 高优先级覆盖低优先级）
SCOPE_PRIORITY = {
    SkillScope.REPO: 2,   # 项目级优先
    SkillScope.USER: 1,   # 用户级次之
    SkillScope.SYSTEM: 0, # 系统级最低（不对用户暴露）
}


def get_user_skills_dir() -> Path:
    """获取用户技能目录"""
    user_dir = Path(os.path.expanduser("~"))
    nexus_skills = user_dir / ".nexus" / "skills"
    nexus_skills.mkdir(parents=True, exist_ok=True)
    return nexus_skills


def get_system_skills_dir() -> Path:
    """获取系统技能目录"""
    return get_user_skills_dir() / ".system"


def get_repo_skills_dir(cwd: Optional[Path] = None) -> Optional[Path]:
    """获取项目技能目录

    Args:
        cwd: 工作目录，默认为当前目录

    Returns:
        项目技能目录路径，如果不存在则返回 None
    """
    cwd = cwd or Path.cwd()
    repo_skills = cwd / ".nexus" / "skills"
    return repo_skills if repo_skills.exists() else None


def get_skill_roots(cwd: Optional[Path] = None) -> list[tuple[SkillScope, Path]]:
    """获取所有 Skill 根目录（按优先级排序）

    优先级：REPO > USER > SYSTEM

    Args:
        cwd: 工作目录

    Returns:
        按优先级排序的作用域和目录列表
    """
    logger.debug(f"Skill: 正在获取技能根目录, cwd={cwd or Path.cwd()}")
    roots: list[tuple[SkillScope, Path]] = []

    # Repo scope (highest priority)
    repo_skills = get_repo_skills_dir(cwd)
    if repo_skills:
        roots.append((SkillScope.REPO, repo_skills))
        logger.debug(f"Skill: 找到项目级技能目录: {repo_skills}")

    # User scope
    user_skills = get_user_skills_dir()
    if user_skills.exists():
        roots.append((SkillScope.USER, user_skills))
        logger.debug(f"Skill: 找到用户级技能目录: {user_skills}")

    # System scope (lowest priority)
    system_skills = get_system_skills_dir()
    if system_skills.exists():
        roots.append((SkillScope.SYSTEM, system_skills))
        logger.debug(f"Skill: 找到系统级技能目录: {system_skills}")

    # Sort by priority (higher priority first)
    roots.sort(key=lambda x: SCOPE_PRIORITY[x[0]], reverse=True)

    logger.info(f"Skill: 技能根目录 (按优先级): {[f'{s.value}:{p}' for s, p in roots]}")
    return roots


def get_skill_scope(skill_dir: Path, cwd: Optional[Path] = None) -> Optional[SkillScope]:
    """判断技能目录所属的作用域

    Args:
        skill_dir: 技能目录路径
        cwd: 工作目录

    Returns:
        作用域，如果不在任何已知作用域则返回 None
    """
    cwd = cwd or Path.cwd()
    skill_dir = skill_dir.resolve()

    # Check repo scope
    repo_skills = get_repo_skills_dir(cwd)
    if repo_skills and skill_dir.resolve() == repo_skills.resolve():
        return SkillScope.REPO

    # Check system scope
    system_skills = get_system_skills_dir()
    if system_skills and skill_dir.is_relative_to(system_skills.resolve()):
        return SkillScope.SYSTEM

    # Check user scope
    user_skills = get_user_skills_dir()
    if user_skills and skill_dir.is_relative_to(user_skills.resolve()):
        return SkillScope.USER

    return None


__all__ = [
    "SkillScope",
    "SCOPE_PRIORITY",
    "get_user_skills_dir",
    "get_system_skills_dir",
    "get_repo_skills_dir",
    "get_skill_roots",
    "get_skill_scope",
]