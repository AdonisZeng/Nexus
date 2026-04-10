"""
Auto Memory Manager - AI-powered selective memory storage"""
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

import yaml

from src.utils.frontmatter import parse_frontmatter


logger = logging.getLogger("Nexus")


# Memory entry type definitions
ENTRY_TYPES = ["decision", "fact", "pattern", "preference"]
CATEGORIES = ["architecture", "coding", "project", "personal"]


@dataclass
class MemoryEntry:
    """Single memory entry."""
    entry_type: str       # decision/fact/pattern/preference
    category: str         # architecture/coding/project/personal
    content: str          # Full memory content
    tags: list[str] = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    session_id: str = ""
    summary: str = ""     # One-line summary for index


class AutoMemoryManager:
    """Manages selective memory storage using LLM decisions."""

    def __init__(self, memory_dir: Optional[Path] = None):
        if memory_dir is None:
            from .core import get_user_memory_dir
            memory_dir = get_user_memory_dir()

        self.memory_dir = memory_dir
        self.entries_dir = self.memory_dir / "entries"
        self.index_path = self.memory_dir / "memory.md"

        # Create entries directory if needed
        self.entries_dir.mkdir(parents=True, exist_ok=True)

    def _generate_entry_filename(self, content: str) -> str:
        """Generate unique filename based on timestamp and content hash."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        hash_suffix = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"{timestamp}_{hash_suffix}.md"

    async def decide_memories(
        self,
        session_messages: list[dict],
        model_adapter
    ) -> list[MemoryEntry]:
        """Use LLM to decide what to remember from the session."""
        formatted = self._format_session_for_analysis(session_messages)

        prompt = f"""你是一个记忆决策助手。分析以下对话，提取值得长期保存的信息。

【记忆类型定义】
- decision: 重要的架构或技术决策
- fact: 事实性的信息（如项目配置、技术栈、已确定的方案）
- pattern: 常见的代码模式或约定
- preference: 用户偏好或团队约定

【提取标准】
只有满足以下条件才值得记忆：
1. 可能在未来重复使用
2. 提供重要上下文
3. 非临时性的内容

【输出格式】
每个记忆按以下格式输出，多个记忆用空行分隔：

TYPE: <type>
CATEGORY: <category>
SUMMARY: <一行总结，不超过50字>
TAGS: <标签，逗号分隔，最多3个>
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
                current["type"] = line_stripped[5:].strip().lower()
            elif line_stripped.startswith("CATEGORY:"):
                current["category"] = line_stripped[9:].strip().lower()
            elif line_stripped.startswith("SUMMARY:"):
                current["summary"] = line_stripped[8:].strip()
            elif line_stripped.startswith("TAGS:"):
                tags_str = line_stripped[5:].strip()
                current["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]
            elif line_stripped.startswith("CONTENT:"):
                in_content = True
                current_content_lines = []
            elif in_content and line_stripped:
                current_content_lines.append(line)

            # Check if we have a complete entry
            if current.get("type") and current.get("category") and current_content_lines:
                content = "\n".join(current_content_lines).strip()
                if len(content) > 20:  # Minimum content length
                    entry = MemoryEntry(
                        entry_type=current.get("type", "fact"),
                        category=current.get("category", "project"),
                        content=content,
                        tags=current.get("tags", []),
                        summary=current.get("summary", content[:80]),
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
        filepath = self.entries_dir / filename

        frontmatter = {
            "type": entry.entry_type,
            "category": entry.category,
            "created": entry.created,
            "session_id": session_id or entry.session_id,
            "tags": entry.tags,
            "summary": entry.summary,
        }

        content_parts = [
            "---",
            yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip(),
            "---",
            "",
            f"# {entry.summary}",
            "",
            entry.content,
        ]

        filepath.write_text("\n".join(content_parts), encoding="utf-8")
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
            index_content = "---\nversion: 1.0\nupdated: {}\n---\n\n# Memory Index\n\nNo memories saved yet.\n".format(
                datetime.now().isoformat()
            )
            self.index_path.write_text(index_content, encoding="utf-8")
            return

        lines = [
            "---",
            "version: 1.0",
            f"updated: {datetime.now().isoformat()}",
            "---",
            "",
            "# Memory Index",
            "",
            f"Total memories: {len(entries)}",
            "",
            "## Recent Memories (last 10)",
            "",
            "| Category | Summary | Tags | Date |",
            "|----------|---------|------|------|",
        ]

        for entry in entries[:10]:
            tags_str = ",".join(entry.tags[:3]) if entry.tags else "-"
            date = entry.created[:10] if len(entry.created) >= 10 else entry.created
            summary = entry.summary[:50] + "..." if len(entry.summary) > 50 else entry.summary
            lines.append(f"| {entry.category} | {summary} | {tags_str} | {date} |")

        lines.extend(["", "## All Memories", ""])

        # Group by category
        for cat in CATEGORIES:
            cat_entries = [e for e in entries if e.category == cat]
            if not cat_entries:
                continue
            lines.append(f"### {cat}")
            for entry in cat_entries:
                date = entry.created[:10] if len(entry.created) >= 10 else entry.created
                lines.append(f"- [{date}] {entry.summary}")
            lines.append("")

        self.index_path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug(f"Auto Memory: updated index with {len(entries)} entries")

    def _parse_entry_file(self, file_path: Path) -> Optional[MemoryEntry]:
        """Parse a memory entry file."""
        try:
            frontmatter, docstring = parse_frontmatter(file_path.read_text(encoding="utf-8"))
            if not frontmatter:
                return None

            summary = frontmatter.get("summary", "")
            if not summary and docstring:
                first_line = docstring.split("\n")[0] if docstring else ""
                summary = first_line[2:].strip() if first_line.startswith("# ") else docstring[:80]

            return MemoryEntry(
                entry_type=frontmatter.get("type", "fact"),
                category=frontmatter.get("category", "project"),
                content=docstring,
                tags=frontmatter.get("tags", []),
                created=frontmatter.get("created", ""),
                session_id=frontmatter.get("session_id", ""),
                summary=summary,
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
            cat_emoji = {
                "architecture": "🏗️",
                "coding": "💻",
                "project": "📁",
                "personal": "👤",
            }.get(entry.category, "📝")

            lines.append(f"**{cat_emoji} {entry.category}**: {entry.summary}")

        return "\n".join(lines)


__all__ = ["AutoMemoryManager", "MemoryEntry"]
