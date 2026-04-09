"""
Context management module - re-exports from agent.context

This module provides backward compatibility and additional context utilities.
The core implementation is in src/agent/context.py.
"""

from src.agent.context import (
    ContextMessage,
    ToolCallEntry,
    ConversationState,
    AgentContext,
    create_context,
    from_messages_list,
)

# Additional utilities specific to context module

from dataclasses import dataclass, field
from typing import Optional
import json
import os
from pathlib import Path
import time
from datetime import datetime


def get_user_memory_dir() -> Path:
    """Get the user memory directory (~/.nexus/memory)"""
    user_dir = Path(os.path.expanduser("~"))
    nexus_memory = user_dir / ".nexus" / "memory"
    # Auto-create if not exists
    nexus_memory.mkdir(parents=True, exist_ok=True)
    return nexus_memory


@dataclass
class SkillMetadata:
    """Metadata for a skill, used for context injection."""
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


class ContextCompressor:
    """Compress context when it exceeds token budget.

    Uses a tiered approach:
    - Keep recent messages intact
    - Summarize middle-aged messages with LLM
    - Extract key decisions from oldest messages
    """

    # Configuration
    KEEP_RECENT = 6          # Keep this many recent messages intact
    SUMMARIZE_THRESHOLD = 12 # Start summarizing after this many messages
    ARCHIVE_THRESHOLD = 20   # Extract key decisions after this many

    def __init__(
        self,
        target_tokens: int = 100000,
        model_adapter=None
    ):
        self.target_tokens = target_tokens
        self.model_adapter = model_adapter

    def should_compress(self, context: AgentContext) -> bool:
        """Check if context should be compressed."""
        return context.total_tokens_used > self.target_tokens

    def compress(self, context: AgentContext) -> AgentContext:
        """Compress context using simple truncation (fallback)."""
        # Keep system messages
        system_messages = [
            m for m in context.short_term_memory
            if m.role == "system"
        ]

        # Keep recent user/assistant messages (last 20)
        recent_messages = [
            m for m in context.short_term_memory
            if m.role in ("user", "assistant")
        ][-20:]

        # Clear and rebuild
        context.short_term_memory = system_messages + recent_messages

        # Update token count estimate
        context.total_tokens_used = sum(m.token_count for m in context.short_term_memory)

        return context

    async def compress_smart(
        self,
        context: AgentContext,
        summarize_fn=None
    ) -> AgentContext:
        """Smart compression using LLM summarization.

        Args:
            context: AgentContext to compress
            summarize_fn: Async function to summarize messages

        Returns:
            Compressed context
        """
        from src.utils.tokenizer import count_tokens

        messages = context.short_term_memory
        if len(messages) <= self.KEEP_RECENT:
            return context

        # Split messages into categories
        recent = messages[-self.KEEP_RECENT:]
        middle = messages[self.ARCHIVE_THRESHOLD:-self.KEEP_RECENT] if len(messages) > self.ARCHIVE_THRESHOLD else []
        oldest = messages[:self.ARCHIVE_THRESHOLD] if len(messages) > self.ARCHIVE_THRESHOLD else []

        compressed = []

        # 1. Extract key decisions from oldest messages
        if oldest and summarize_fn:
            try:
                key_decisions = await self._extract_key_decisions(oldest, summarize_fn)
                if key_decisions:
                    compressed.append(type(recent[0])(
                        role="system",
                        content=f"[Earlier conversation summary]\n{key_decisions}",
                        token_count=count_tokens(key_decisions)
                    ))
            except Exception as e:
                import logging
                logging.getLogger("Nexus").warning(f"Failed to extract key decisions: {e}")

        # 2. Summarize middle messages
        if middle and summarize_fn:
            try:
                summary = await summarize_fn(middle)
                if summary:
                    compressed.append(type(recent[0])(
                        role="system",
                        content=f"[Previous conversation summary]\n{summary}",
                        token_count=count_tokens(summary)
                    ))
            except Exception as e:
                import logging
                logging.getLogger("Nexus").warning(f"Failed to summarize messages: {e}")

        # 3. Keep recent messages intact
        compressed.extend(recent)

        context.short_term_memory = compressed
        context.total_tokens_used = sum(
            m.token_count for m in context.short_term_memory
        )

        return context

    async def _extract_key_decisions(
        self,
        messages: list,
        summarize_fn
    ) -> str:
        """Extract key decisions from older messages."""
        # Format messages for summarization
        conversation = self._format_messages(messages)

        prompt = f"""Analyze this conversation and extract KEY DECISIONS made.
Focus on:
- Architecture choices
- Implementation approaches selected
- Scope boundaries defined
- Tradeoffs accepted

Return ONLY the key decisions as bullet points. Be concise.

Conversation:
{conversation}
"""

        try:
            response = await summarize_fn(prompt)
            return response if response else None
        except Exception:
            return None

    def _format_messages(self, messages: list) -> str:
        """Format messages for LLM summarization."""
        lines = []
        for msg in messages:
            role = msg.role.upper()
            content = msg.content[:500] if hasattr(msg, 'content') else str(msg)[:500]
            lines.append(f"{role}: {content}")
        return "\n\n".join(lines)


class LLMContextCompressor:
    """LLM-powered context summarization.

    Single authoritative implementation replacing the three duplicate copies:
      - NexusCLI._compress_context_llm  (cli/main.py)
      - SubagentRunner._compress_context_llm  (tools/subagent/runner.py)
      - AgentLoop.summarize_and_compress  (agent/loop.py, kept for callback compat)
    """

    SUMMARIZE_PROMPT = """你是一个上下文压缩助手。请提炼以下对话的精简摘要，\
保留关键信息、决策、进展和重要细节。摘要应该简洁但信息完整。

对话内容：
{conversation}

请直接返回摘要内容，不需要额外解释。摘要格式：
[对话摘要]
- 关键主题：xxx
- 重要进展：xxx
- 待处理事项：xxx
- 关键细节：xxx
"""

    @staticmethod
    async def compress_messages(
        messages: list[dict],
        adapter,
        min_non_system: int = 2,
    ) -> "list[dict] | None":
        """Compress a flat list[dict] message history via LLM summarization.

        Used by NexusCLI (operates on list[dict] format).

        @param messages  The current message history
        @param adapter   A ModelAdapter instance with .chat() method
        @param min_non_system  Minimum non-system messages required to trigger compression
        @return New compressed message list, or None if compression was skipped/failed
        """
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) <= min_non_system:
            return None

        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')[:500]}"
            for m in non_system
        )
        prompt = LLMContextCompressor.SUMMARIZE_PROMPT.format(conversation=conversation)

        try:
            response = await adapter.chat(
                [{"role": "user", "content": prompt}], ""
            )
            if not response:
                return None
            summary_msg = {"role": "system", "content": f"[对话摘要]\n{response}"}
            return system_msgs + [summary_msg]
        except Exception:
            return None

    @staticmethod
    async def compress_context(
        context,
        adapter,
        min_non_system: int = 2,
    ) -> bool:
        """Compress an AgentContext's short_term_memory via LLM summarization.

        Used by SubagentRunner and AgentLoop (operates on AgentContext format).

        @param context   An AgentContext instance
        @param adapter   A ModelAdapter instance with .chat() method
        @param min_non_system  Minimum non-system messages required to trigger compression
        @return True if compression succeeded, False otherwise
        """
        msgs = context.short_term_memory
        system_msgs = [m for m in msgs if m.role == "system"]
        non_system = [m for m in msgs if m.role != "system"]

        if len(non_system) <= min_non_system:
            return False

        conversation = "\n".join(
            f"{m.role}: {m.content[:500]}" for m in non_system
        )
        prompt = LLMContextCompressor.SUMMARIZE_PROMPT.format(conversation=conversation)

        try:
            response = await adapter.chat(
                [{"role": "user", "content": prompt}], ""
            )
            if not response:
                return False
            # Import here to avoid circular import at module level
            from src.agent.context import ContextMessage
            summary = ContextMessage(
                role="system",
                content=f"[对话摘要]\n{response}",
                token_count=len(response) // 4,
            )
            context.short_term_memory = system_msgs + [summary]
            return True
        except Exception:
            return False


class SessionPersistence:
    """Save and load session state."""

    @staticmethod
    def save(context: AgentContext, path: str) -> None:
        """Save context to file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(context.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: str) -> AgentContext:
        """Load context from file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AgentContext.from_dict(data)


@dataclass
class SessionSummary:
    """Session metadata for listing."""
    session_id: str
    filename: str
    created_at: float
    title: str
    message_count: int


class MemoryManager:
    """Manage persistent memory/session storage in ~/.nexus/memory"""

    def __init__(self, memory_dir: Optional[Path] = None):
        self.memory_dir = memory_dir or get_user_memory_dir()

    def _generate_filename(self, session_id: str) -> str:
        """Generate filename from session_id"""
        return f"{session_id}.md"

    def _generate_title_from_messages(self, messages: list[dict]) -> str:
        """Generate a title from the first user message"""
        if not messages:
            return "新对话"
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")[:50]
                return content + "..." if len(msg.get("content", "")) > 50 else content
        return "新对话"

    def save_session(self, session_id: str, messages: list[dict], title: Optional[str] = None) -> Path:
        """Save session as a markdown file"""
        if title is None:
            title = self._generate_title_from_messages(messages)

        filename = self._generate_filename(session_id)
        filepath = self.memory_dir / filename

        # Build markdown content
        lines = [
            f"# {title}",
            "",
            f"**Session ID**: {session_id}",
            f"**创建时间**: {datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            ""
        ]

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                lines.append(f"### System")
            elif role == "user":
                lines.append(f"### User")
            elif role == "assistant":
                lines.append(f"### Assistant")
            elif role == "tool":
                tool_name = msg.get("metadata", {}).get("tool_name", "tool")
                lines.append(f"### Tool ({tool_name})")

            lines.append(content)
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return filepath

    def list_sessions(self) -> list[SessionSummary]:
        """List all saved sessions"""
        sessions = []

        for md_file in self.memory_dir.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                lines = content.split("\n")

                title = "未命名会话"
                session_id = md_file.stem
                created_at = md_file.stat().st_mtime

                # Parse title from first heading
                if lines and lines[0].startswith("# "):
                    title = lines[0][2:].strip()

                # Count messages (### headings)
                message_count = sum(1 for line in lines if line.strip().startswith("### "))

                sessions.append(SessionSummary(
                    session_id=session_id,
                    filename=md_file.name,
                    created_at=created_at,
                    title=title,
                    message_count=message_count
                ))
            except Exception:
                continue

        # Sort by creation time, newest first
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def load_session(self, session_id: str) -> Optional[list[dict]]:
        """Load session messages from markdown file"""
        filepath = self.memory_dir / self._generate_filename(session_id)

        if not filepath.exists():
            return None

        try:
            content = filepath.read_text(encoding="utf-8")
            messages = []
            current_role = None
            current_content = []

            lines = content.split("\n")
            in_header = True

            for line in lines:
                # Skip header section
                if in_header:
                    if line.startswith("---"):
                        in_header = False
                    continue

                # Check for role marker
                if line.strip().startswith("### "):
                    # Save previous message
                    if current_role and current_content:
                        messages.append({
                            "role": current_role,
                            "content": "\n".join(current_content).strip()
                        })

                    # Parse new role
                    role_line = line.strip()[4:]
                    if "System" in role_line:
                        current_role = "system"
                    elif "User" in role_line:
                        current_role = "user"
                    elif "Assistant" in role_line:
                        current_role = "assistant"
                    elif "Tool" in role_line:
                        current_role = "tool"
                    else:
                        current_role = "user"

                    current_content = []
                elif line.strip():
                    current_content.append(line)
                elif current_role and current_content:
                    # Empty line but we have content - could be paragraph break
                    pass

            # Don't forget last message
            if current_role and current_content:
                messages.append({
                    "role": current_role,
                    "content": "\n".join(current_content).strip()
                })

            return messages
        except Exception:
            return None

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file"""
        filepath = self.memory_dir / self._generate_filename(session_id)
        if filepath.exists():
            filepath.unlink()
            return True
        return False


__all__ = [
    "ContextMessage",
    "ToolCallEntry",
    "ConversationState",
    "AgentContext",
    "SkillMetadata",
    "ContextCompressor",
    "SessionPersistence",
    "SessionSummary",
    "MemoryManager",
    "get_user_memory_dir",
    "create_context",
    "from_messages_list",
]