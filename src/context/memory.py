"""Context memory utilities.

Provides LLMContextCompressor, MemoryManager and the user memory directory.
The core context implementation lives in src/agent/context.py.
"""

from dataclasses import dataclass
from typing import Optional
import os
from pathlib import Path
import time
from datetime import datetime

# Re-exported for convenience
from src.agent.context import (
    ContextMessage,
    ToolCallEntry,
    ConversationState,
    AgentContext,
    create_context,
    from_messages_list,
)


def get_user_memory_dir() -> Path:
    """Get the user memory directory (~/.nexus/memory)"""
    user_dir = Path(os.path.expanduser("~"))
    nexus_memory = user_dir / ".nexus" / "memory"
    # Auto-create if not exists
    nexus_memory.mkdir(parents=True, exist_ok=True)
    return nexus_memory


class LLMContextCompressor:
    """LLM-powered context summarization.

    Single authoritative implementation used by:
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
    "LLMContextCompressor",
    "SessionSummary",
    "MemoryManager",
    "get_user_memory_dir",
    "create_context",
    "from_messages_list",
]
