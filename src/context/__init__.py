"""Context management module for Nexus Agent.

This module provides:
- ContextMessage: Individual message with metadata
- ConversationState: State tracking for the conversation
- AgentContext: Main context container with short/long term memory
- SkillMetadata: Metadata for skills
- ContextCompressor: Context compression/summarization
- SessionPersistence: Session save/load functionality
- MemoryManager: Persistent memory management in ~/.nexus/memory
- get_user_memory_dir: Get the memory directory path

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

from .core import (
    SkillMetadata,
    ContextCompressor,
    SessionPersistence,
    SessionSummary,
    MemoryManager,
    get_user_memory_dir,
)
from .nexus_md import NexusMDLoader, NexusMD, NexusMDMetadata
from .auto_memory import AutoMemoryManager, MemoryEntry

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
    # New for Phase 1
    "NexusMDLoader",
    "NexusMD",
    "NexusMDMetadata",
    "AutoMemoryManager",
    "MemoryEntry",
]