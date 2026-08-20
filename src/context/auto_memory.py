"""
Auto Memory Manager - AI-powered selective memory storage"""

import hashlib
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

import yaml

from src.utils.frontmatter import parse_frontmatter, serialize_frontmatter


logger = logging.getLogger("Nexus")


# Memory type definitions (replaces entry_type + category)
MEMORY_TYPES = ["user", "feedback", "project", "reference"]

# Emoji mapping for memory types in prompt display
MEMORY_TYPE_EMOJI = {
    "user": "👤",
    "feedback": "💬",
    "project": "📁",
    "reference": "🔗",
}

# Memory guidance for LLM decision-making
MEMORY_GUIDANCE = """
When to save memories:
- User states a preference ("I like tabs", "always use pytest") -> type: user
- User corrects you ("don't do X", "that was wrong because...") -> type: feedback
- You learn a project fact that is not easy to infer from current code alone
  (for example: a rule exists because of compliance, or a legacy module must
  stay untouched for business reasons) -> type: project
- You learn where an external resource lives (ticket board, dashboard, docs URL)
  -> type: reference
When NOT to save:
- Anything easily derivable from code (function signatures, file structure, directory layout)
- Temporary task state (current branch, open PR numbers, current TODOs)
- Secrets or credentials (API keys, passwords)
"""


@dataclass
class MemoryEntry:
    """Single memory entry."""
    memory_type: str      # user/feedback/project/reference
    content: str          # Full memory content
    tags: list[str] = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    session_id: str = ""
    summary: str = ""     # One-line summary for index
    memory_scope: str = "cross_session"  # session/cross_session


class AutoMemoryManager:
    """Manages selective memory storage using LLM decisions."""

    def __init__(self, memory_dir: Optional[Path] = None):
        if memory_dir is None:
            from .core import get_user_memory_dir
            memory_dir = get_user_memory_dir()

        self.memory_dir = memory_dir
        self.entries_dir = self.memory_dir / "entries"
        self.session_dir = self.memory_dir / "session_entries"
        self.index_path = self.memory_dir / "memory.md"

        # Create entries directories if needed
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _generate_entry_filename(self, content: str) -> str:
        """Generate unique filename based on timestamp and content hash."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        hash_suffix = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"{timestamp}_{hash_suffix}.md"

    def get_guidance(self) -> str:
        """Return memory guidance for system prompt injection."""
        return MEMORY_GUIDANCE

    async def decide_memories(
        self,
        session_messages: list[dict],
        model_adapter
    ) -> list[MemoryEntry]:
        """Use LLM to decide what to remember from the session."""
        formatted = self._format_session_for_analysis(session_messages)

        prompt = f"""{MEMORY_GUIDANCE}

【记忆类型定义】
- user: 用户偏好 ("I like tabs", "always use pytest")
- feedback: 用户纠正或批评 ("don't do X", "that was wrong because...")
- project: 非显而易见项目事实 (合规原因、遗留模块必须保留等)
- reference: 外部资源位置 (ticket board、dashboard、文档URL)

【记忆范围】
- session: 仅当前会话有用，不值得长期保存
- cross_session: 值得在多个会话间保持

【输出格式】
每个记忆按以下格式输出，多个记忆用空行分隔：

TYPE: <type>
SUMMARY: <一行总结，不超过50字>
TAGS: <标签，逗号分隔，最多3个>
SCOPE: <session/cross_session>
CONTENT:
<完整记忆内容，要详细到足以在未来独立理解>

如果没有任何值得记忆的内容，输出：
NONE
只在确实没有任何有价值信息时才输出NONE。

---
对话内容：
{formatted}
"""

        try:
            response = await model_adapter.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=""
            )
            entries = self._parse_memory_response(response)
            logger.info(f"Auto Memory: LLM decided {len(entries)} memories worth saving")
            return entries
        except Exception as e:
            logger.warning(f"Auto Memory: LLM decision failed: {e}")
            return []

    def _format_session_for_analysis(
        self,
        messages: list[dict],
        max_chars: int = 6000
    ) -> str:
        """Format session messages for LLM analysis."""
        lines = []
        total_chars = 0

        # Process from end, skip system messages
        meaningful = [m for m in messages if m.get("role") != "system"]

        for msg in reversed(meaningful):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Truncate long content
            if len(content) > 600:
                content = content[:600] + "..."

            msg_text = f"{role.upper()}: {content}\n"

            if total_chars + len(msg_text) > max_chars:
                break

            lines.append(msg_text)
            total_chars += len(msg_text)

        return "\n".join(reversed(lines))

    def _parse_memory_response(self, response: str) -> list[MemoryEntry]:
        """Parse LLM response into MemoryEntry objects."""
        entries = []

        if not response or response.strip().upper().startswith("NONE"):
            return entries

        current: dict = {}
        current_content_lines: list[str] = []
        in_content = False

        for line in response.strip().split("\n"):
            line_stripped = line.strip()

            if line_stripped.upper() == "NONE":
                break

            if line_stripped.startswith("TYPE:"):
                current["memory_type"] = line_stripped[5:].strip().lower()
            elif line_stripped.startswith("SUMMARY:"):
                current["summary"] = line_stripped[8:].strip()
            elif line_stripped.startswith("TAGS:"):
                tags_str = line_stripped[5:].strip()
                current["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]
            elif line_stripped.startswith("SCOPE:"):
                current["scope"] = line_stripped[6:].strip().lower()
            elif line_stripped.startswith("CONTENT:"):
                in_content = True
                current_content_lines = []
            elif in_content and line_stripped:
                current_content_lines.append(line)

            # Check if we have a complete entry
            if current.get("memory_type") and current_content_lines:
                content = "\n".join(current_content_lines).strip()
                if len(content) > 20:  # Minimum content length
                    entry = MemoryEntry(
                        memory_type=current.get("memory_type", "project"),
                        content=content,
                        tags=current.get("tags", []),
                        summary=current.get("summary", content[:80]),
                        session_id="",  # Will be set by caller
                        memory_scope=current.get("scope", "cross_session"),
                    )
                    entries.append(entry)

                # Reset for next entry
                current = {}
                current_content_lines = []
                in_content = False

        return entries

    def save_entry(self, entry: MemoryEntry, session_id: str = "") -> Path:
        """Save a single memory entry to a file."""
        filename = self._generate_entry_filename(entry.content)

        # Choose directory based on scope
        if entry.memory_scope == "session":
            target_dir = self.session_dir
        else:
            target_dir = self.entries_dir

        filepath = target_dir / filename

        frontmatter = {
            "memory_type": entry.memory_type,
            "created": entry.created,
            "session_id": session_id or entry.session_id,
            "tags": entry.tags,
            "summary": entry.summary,
            "scope": entry.memory_scope,
        }

        filepath.write_text(
            serialize_frontmatter(frontmatter, entry.content, entry.summary),
            encoding="utf-8"
        )
        logger.debug(f"Auto Memory: saved entry to {filepath}")
        return filepath

    def update_index(self) -> None:
        """Update the memory.md index file."""
        entries = []

        for entry_file in sorted(self.entries_dir.glob("*.md"), reverse=True):
            entry = self._parse_entry_file(entry_file)
            if entry:
                entries.append(entry)

        if not entries:
            # No entries, write minimal index
            index_content = "---\nversion: 2.0\nupdated: {}\n---\n\n# Memory Index\n\nNo memories saved yet.\n".format(
                datetime.now().isoformat()
            )
            self.index_path.write_text(index_content, encoding="utf-8")
            return

        lines = [
            "---",
            "version: 2.0",
            f"updated: {datetime.now().isoformat()}",
            "---",
            "",
            "# Memory Index",
            "",
            f"Total memories: {len(entries)}",
            "",
            "## Recent Memories (last 10)",
            "",
            "| Type | Scope | Summary | Tags | Date |",
            "|------|-------|---------|------|------|",
        ]

        for entry in entries[:10]:
            tags_str = ",".join(entry.tags[:3]) if entry.tags else "-"
            date = entry.created[:10] if len(entry.created) >= 10 else entry.created
            summary = entry.summary[:50] + "..." if len(entry.summary) > 50 else entry.summary
            lines.append(f"| {entry.memory_type} | {entry.memory_scope} | {summary} | {tags_str} | {date} |")

        lines.extend(["", "## All Memories", ""])

        # Group by memory_type
        for mem_type in MEMORY_TYPES:
            type_entries = [e for e in entries if e.memory_type == mem_type]
            if not type_entries:
                continue
            lines.append(f"### {mem_type}")
            for entry in type_entries:
                date = entry.created[:10] if len(entry.created) >= 10 else entry.created
                lines.append(f"- [{date}] {entry.summary}")
            lines.append("")

        self.index_path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug(f"Auto Memory: updated index with {len(entries)} entries")

    def _parse_entry_file(self, file_path: Path) -> Optional[MemoryEntry]:
        """Parse a memory entry file with backward compatibility for old formats."""
        try:
            frontmatter, docstring = parse_frontmatter(file_path.read_text(encoding="utf-8"))
            if not frontmatter:
                return None

            # Backward compatibility: handle old format (type + category)
            memory_type = frontmatter.get("memory_type") or frontmatter.get("type", "project")

            # Old category field is dropped - it was redundant with memory_type
            scope = frontmatter.get("scope", "cross_session")

            summary = frontmatter.get("summary", "")
            if not summary and docstring:
                first_line = docstring.split("\n")[0] if docstring else ""
                summary = first_line[2:].strip() if first_line.startswith("# ") else docstring[:80]

            return MemoryEntry(
                memory_type=memory_type,
                content=docstring,
                tags=frontmatter.get("tags", []),
                created=frontmatter.get("created", ""),
                session_id=frontmatter.get("session_id", ""),
                summary=summary,
                memory_scope=scope,
            )
        except Exception as e:
            logger.warning(f"Auto Memory: failed to parse entry {file_path}: {e}")
            return None

    async def process_session(
        self,
        session_messages: list[dict],
        session_id: str,
        model_adapter
    ) -> int:
        """Main entry point: decide, save, and update index."""
        # Decide what to remember
        entries = await self.decide_memories(session_messages, model_adapter)

        if not entries:
            logger.info("Auto Memory: no memories worth saving")
            return 0

        # Save each entry
        for entry in entries:
            entry.session_id = session_id
            self.save_entry(entry, session_id)

        # Update index
        self.update_index()

        logger.info(f"Auto Memory: processed {len(entries)} memories")
        return len(entries)

    def get_recent_memories(self, limit: int = 5) -> list[MemoryEntry]:
        """Load recent memories for context injection."""
        entries = []

        # Get entries from both directories
        all_files = sorted(
            list(self.entries_dir.glob("*.md")) + list(self.session_dir.glob("*.md")),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )[:limit]

        for entry_file in all_files:
            entry = self._parse_entry_file(entry_file)
            if entry:
                entries.append(entry)

        return entries

    def get_cross_session_memories(self, limit: int = 20) -> list[MemoryEntry]:
        """Load cross-session memories only (for loading into AgentContext)."""
        entries = []

        for entry_file in sorted(self.entries_dir.glob("*.md"), reverse=True)[:limit]:
            entry = self._parse_entry_file(entry_file)
            if entry:
                entries.append(entry)

        return entries

    def get_memories_section(self, limit: int = 5) -> str:
        """Get memories formatted for system prompt injection."""
        entries = self.get_recent_memories(limit)

        if not entries:
            return ""

        lines = ["## 长期记忆", ""]

        for entry in entries:
            emoji = MEMORY_TYPE_EMOJI.get(entry.memory_type, "📝")
            lines.append(f"**{emoji} {entry.memory_type}**: {entry.summary}")

        return "\n".join(lines)

    def trigger_consolidation(self, model_adapter=None) -> bool:
        """
        Trigger memory consolidation if conditions are met.
        Called after session processing.

        Returns True if consolidation was triggered.
        """
        from .consolidator import get_consolidator

        consolidator = get_consolidator(self.memory_dir)
        can_run, reason = consolidator.should_consolidate()

        if not can_run:
            logger.debug(f"AutoMemoryManager: Consolidation skipped: {reason}")
            return False

        # Run consolidation asynchronously
        if model_adapter:
            import asyncio
            asyncio.create_task(consolidator.consolidate(model_adapter))
            logger.info("AutoMemoryManager: Consolidation scheduled")
            return True
        else:
            logger.debug("AutoMemoryManager: Consolidation requires model_adapter")
            return False


__all__ = ["AutoMemoryManager", "MemoryEntry", "MEMORY_GUIDANCE"]
