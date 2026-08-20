"""Skills: SKILL.md parsing, loading scopes and trigger matching (tmp dirs)."""
import pytest

from src.skills.loader import SkillMetadata, SKILLParser, SkillLoader, SkillCatalog
from src.skills.matcher import SkillMatcher, AutoSkillMatcher
from src.skills.scope import SkillScope, get_skill_roots

SKILL_MD = """---
name: deploy
description: Deploy the app to production server
trigger:
  - deploy app
aliases:
  - ship
---
Deploy instructions body.
"""


def _make_skill(root, skill_name="deploy", content=SKILL_MD):
    d = root / skill_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d / "SKILL.md"


class TestSKILLParser:
    def test_parse_frontmatter_and_body(self, tmp_path):
        path = _make_skill(tmp_path)
        fm, body = SKILLParser.parse(path)
        assert fm["name"] == "deploy"
        assert fm["trigger"] == ["deploy app"]
        assert body == "Deploy instructions body."

    def test_to_skill_metadata(self, tmp_path):
        path = _make_skill(tmp_path)
        meta = SKILLParser.to_skill_metadata(path)
        assert meta.name == "deploy"
        assert meta.triggers == ["deploy app"]
        assert meta.aliases == ["ship"]
        assert meta.file_path == path

    def test_name_defaults_to_directory(self, tmp_path):
        path = _make_skill(
            tmp_path, "mydir",
            "---\ndescription: no name here\n---\nbody",
        )
        meta = SKILLParser.to_skill_metadata(path)
        assert meta.name == "mydir"


class TestSkillLoader:
    def test_scan_directory_finds_skill_md(self, tmp_path):
        _make_skill(tmp_path, "a")
        (tmp_path / "no_skill").mkdir()  # dir without SKILL.md
        (tmp_path / "loose.md").write_text("x", encoding="utf-8")
        found = SkillLoader().scan_directory(tmp_path)
        assert len(found) == 1
        assert found[0].parent.name == "a"

    def test_scan_missing_directory(self, tmp_path):
        assert SkillLoader().scan_directory(tmp_path / "nope") == []

    def test_load_all_repo_scope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        repo_skills = tmp_path / ".nexus" / "skills"
        _make_skill(repo_skills, "deploy")
        skills = SkillLoader().load_all()
        assert [s.name for s in skills] == ["deploy"]

    def test_load_all_dedupes_by_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        repo_skills = tmp_path / ".nexus" / "skills"
        _make_skill(repo_skills, "deploy")
        # user scope (~ under hermetic HOME) with same name
        from src.skills.scope import get_user_skills_dir
        user_dir = get_user_skills_dir()
        _make_skill(user_dir, "deploy")
        skills = SkillLoader().load_all()
        assert len(skills) == 1
        # repo scope has higher priority
        assert "repo" in str(skills[0].file_path) or str(repo_skills) in str(skills[0].file_path)

    def test_skill_roots_priority(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".nexus" / "skills").mkdir(parents=True)
        roots = get_skill_roots()
        scopes = [s for s, _ in roots]
        assert SkillScope.REPO in scopes
        # repo first when present
        assert scopes[0] == SkillScope.REPO


class TestSkillCatalog:
    def test_catalog_describe_and_load(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_skill(tmp_path / ".nexus" / "skills", "deploy")
        catalog = SkillCatalog()
        text = catalog.describe_available()
        assert "deploy" in text
        assert "Deploy the app" in text

        full = catalog.load_full_text("deploy")
        assert "Deploy instructions body." in full

    def test_unknown_skill_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        catalog = SkillCatalog()
        assert "Error: Unknown skill" in catalog.load_full_text("ghost")


class TestSkillMatcher:
    def _skills(self):
        return [SkillMetadata(
            name="deploy",
            description="Deploy the app to production server",
            triggers=["deploy app"],
            aliases=["ship"],
        )]

    def test_exact_trigger_match(self):
        result = SkillMatcher().match("please deploy app now", self._skills())
        assert result is not None
        assert result.skill.name == "deploy"
        assert result.confidence == 1.0  # 1.0 + 0.2 bonus capped at 1.0

    def test_word_boundary_match(self):
        result = SkillMatcher().match("I want to deploy today", self._skills())
        assert result is not None
        assert result.confidence >= 0.8

    def test_alias_match(self):
        result = SkillMatcher().match("ship it", self._skills())
        assert result is not None
        assert result.confidence >= 0.8

    def test_description_keyword_match(self):
        result = SkillMatcher().match("check production logs", self._skills())
        assert result is not None
        assert result.confidence >= 0.3

    def test_no_match_below_threshold(self):
        assert SkillMatcher().match("zzz qqq", self._skills()) is None

    def test_empty_inputs(self):
        matcher = SkillMatcher()
        assert matcher.match("", self._skills()) is None
        assert matcher.match("deploy app", []) is None

    def test_match_all_sorted_by_confidence(self):
        skills = self._skills() + [SkillMetadata(
            name="other",
            description="something about production",
            triggers=[],
        )]
        results = SkillMatcher().match_all("deploy app on production", skills)
        assert len(results) >= 1
        confidences = [r.confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)
        assert results[0].skill.name == "deploy"

    def test_auto_skill_matcher(self):
        auto = AutoSkillMatcher(self._skills())
        assert auto.find_skill("deploy app").name == "deploy"
        assert auto.find_skill("zzz qqq") is None
        assert "deploy" in auto.get_available_skills_description()
