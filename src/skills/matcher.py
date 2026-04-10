"""Skill matcher - match user input to skills based on triggers"""

import re
from dataclasses import dataclass, field
from typing import Optional
from difflib import SequenceMatcher

from .loader import SkillMetadata


@dataclass
class MatchResult:
    """Result of skill matching"""
    skill: SkillMetadata
    confidence: float  # 0.0 to 1.0
    matched_triggers: list[str] = field(default_factory=list)
    reason: str = ""


class SkillMatcher:
    """Match user input to skills based on triggers and keywords"""

    def __init__(self, case_sensitive: bool = False):
        self.case_sensitive = case_sensitive

    def match(
        self,
        user_input: str,
        skills: list[SkillMetadata],
        threshold: float = 0.3,
    ) -> Optional[MatchResult]:
        """
        Find the best matching skill for user input.

        Args:
            user_input: The user's input text
            skills: List of available skills
            threshold: Minimum confidence threshold (0.0 to 1.0)

        Returns:
            MatchResult if a match is found, None otherwise
        """
        if not user_input or not skills:
            return None

        user_input_normalized = user_input if self.case_sensitive else user_input.lower()

        best_match: Optional[MatchResult] = None

        for skill in skills:
            result = self._match_skill(user_input_normalized, skill)
            if result and (best_match is None or result.confidence > best_match.confidence):
                if result.confidence >= threshold:
                    best_match = result

        return best_match

    def _match_skill(self, user_input: str, skill: SkillMetadata) -> Optional[MatchResult]:
        """Match a single skill against user input"""
        matched_triggers = []
        max_confidence = 0.0

        # Check triggers (highest priority)
        for trigger in skill.triggers:
            trigger_normalized = trigger if self.case_sensitive else trigger.lower()
            confidence, is_exact = self._calculate_trigger_match(user_input, trigger_normalized)

            if confidence > 0:
                matched_triggers.append(trigger)
                max_confidence = max(max_confidence, confidence)

                # Exact match gets a bonus
                if is_exact:
                    max_confidence = min(1.0, max_confidence + 0.2)

        # Check aliases
        for alias in skill.aliases:
            alias_normalized = alias if self.case_sensitive else alias.lower()
            if alias_normalized in user_input:
                matched_triggers.append(alias)
                max_confidence = max(max_confidence, 0.8)

        # Check description keywords
        if skill.description:
            desc_normalized = skill.description if self.case_sensitive else skill.description.lower()
            keyword_matches = self._match_keywords(user_input, desc_normalized)
            if keyword_matches:
                max_confidence = max(max_confidence, 0.3 + keyword_matches * 0.1)

        if max_confidence > 0:
            return MatchResult(
                skill=skill,
                confidence=max_confidence,
                matched_triggers=matched_triggers,
                reason=f"Matched {len(matched_triggers)} triggers" if matched_triggers else "Keyword match"
            )

        return None

    def _calculate_trigger_match(self, user_input: str, trigger: str) -> tuple[float, bool]:
        """Calculate match confidence for a trigger"""
        # Exact match
        if trigger in user_input:
            return 1.0, True

        # Word boundary match
        trigger_words = trigger.split()
        if any(tw in user_input for tw in trigger_words):
            return 0.8, False

        # Fuzzy match using sequence ratio
        ratio = SequenceMatcher(None, trigger, user_input).ratio()
        if ratio > 0.6:
            return ratio, False

        # Check if trigger is a significant substring
        if len(trigger) > 3 and trigger in user_input:
            return 0.7, False

        return 0.0, False

    def _match_keywords(self, user_input: str, description: str) -> int:
        """Count keyword matches between input and description"""
        # Extract potential keywords from description
        # Simple approach: words longer than 4 characters
        desc_words = set(w.strip(".,!?;:") for w in description.split() if len(w) > 4)
        input_words = set(w.strip(".,!?;:") for w in user_input.split())

        matches = desc_words & input_words
        return len(matches)

    def match_all(
        self,
        user_input: str,
        skills: list[SkillMetadata],
        threshold: float = 0.3,
    ) -> list[MatchResult]:
        """
        Find all matching skills for user input, sorted by confidence.

        Args:
            user_input: The user's input text
            skills: List of available skills
            threshold: Minimum confidence threshold

        Returns:
            List of MatchResult, sorted by confidence (highest first)
        """
        if not user_input or not skills:
            return []

        user_input_normalized = user_input if self.case_sensitive else user_input.lower()
        results = []

        for skill in skills:
            result = self._match_skill(user_input_normalized, skill)
            if result and result.confidence >= threshold:
                results.append(result)

        return sorted(results, key=lambda r: r.confidence, reverse=True)


class AutoSkillMatcher:
    """Automatically match skills based on user input"""

    def __init__(self, skills: list[SkillMetadata]):
        self.skills = skills
        self.matcher = SkillMatcher()

    def find_skill(self, user_input: str, threshold: float = 0.5) -> Optional[SkillMetadata]:
        """Find the best matching skill for user input"""
        result = self.matcher.match(user_input, self.skills, threshold)
        return result.skill if result else None

    def get_available_skills_description(self) -> str:
        """Get a description of all available skills for system prompt"""
        lines = ["Available skills:"]
        for skill in self.skills:
            triggers = ", ".join(skill.triggers[:3]) if skill.triggers else "no triggers"
            lines.append(f"- {skill.name}: {skill.description} (triggers: {triggers})")
        return "\n".join(lines)


def create_skill_matcher_from_directory(skills_dir: str) -> AutoSkillMatcher:
    """Create an AutoSkillMatcher by loading skills from a directory"""
    from .loader import SkillLoader
    from pathlib import Path

    loader = SkillLoader(Path(skills_dir))
    skills = loader.load_all()
    return AutoSkillMatcher(skills)


__all__ = [
    "MatchResult",
    "SkillMatcher",
    "AutoSkillMatcher",
    "create_skill_matcher_from_directory",
]