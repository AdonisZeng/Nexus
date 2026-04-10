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
    LLMContextCompressor,
    UnifiedContextCompressor,
    get_unified_compressor,
    SessionPersistence,
    SessionSummary,
    MemoryManager,
    get_user_memory_dir,
)
from .nexus_md import NexusMDLoader, NexusMD, NexusMDMetadata
from .auto_memory import AutoMemoryManager, MemoryEntry
from .tool_persister import (
    ToolOutputPersister,
    PersistedOutput,
    PersistConfig,
    persist_tool_output,
    get_persister,
)
from .micro_compactor import (
    MicroCompactor,
    MicroCompactConfig,
    micro_compact_messages,
    get_compactor,
)
from .consolidator import MemoryConsolidator, get_consolidator

__all__ = [
    "ContextMessage",
    "ToolCallEntry",
    "ConversationState",
    "AgentContext",
    "SkillMetadata",
    "ContextCompressor",
    "LLMContextCompressor",
    "UnifiedContextCompressor",
    "get_unified_compressor",
    "SessionPersistence",
    "SessionSummary",
    "MemoryManager",
    "get_user_memory_dir",
    "create_context",
    "from_messages_list",
    "NexusMDLoader",
    "NexusMD",
    "NexusMDMetadata",
    "AutoMemoryManager",
    "MemoryEntry",
    # Tier-1
    "ToolOutputPersister",
    "PersistedOutput",
    "PersistConfig",
    "persist_tool_output",
    "get_persister",
    # Tier-2
    "MicroCompactor",
    "MicroCompactConfig",
    "micro_compact_messages",
    "get_compactor",
    # Consolidation
    "MemoryConsolidator",
    "get_consolidator",
]