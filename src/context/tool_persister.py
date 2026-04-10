"""
Tool Output Persister - Tier 1 of context compression system.

Persists large tool outputs (>threshold) to disk and returns a preview.
Storage: ~/.nexus/tool-results/
Metadata: ~/.nexus/tool-results/.metadata.jsonl
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json
import time
import logging

logger = logging.getLogger("Nexus")

# Default storage directory under user home
TOOL_RESULTS_DIR = Path.home() / ".nexus" / "tool-results"


@dataclass
class PersistedOutput:
    """Result of persisting a tool output."""
    preview: str               # Preview text (original if below threshold)
    persisted_path: Optional[Path]  # Path to persisted file, None if not persisted
    was_persisted: bool       # True if output was persisted to disk
    original_size: int        # Original output size in chars


@dataclass
class PersistConfig:
    """Configuration for tool output persistence."""
    threshold: int = 20000           # Chars > threshold trigger persistence
    preview_chars: int = 2000        # Characters to keep in preview
    storage_dir: Path = TOOL_RESULTS_DIR
    max_stored_size_mb: int = 100    # Maximum total storage size (future use)


class ToolOutputPersister:
    """Persists large tool outputs to disk and returns preview."""

    def __init__(self, config: Optional[PersistConfig] = None):
        self.config = config or PersistConfig()
        self.config.storage_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_file = self.config.storage_dir / ".metadata.jsonl"

    def persist(self, tool_use_id: str, output: str) -> PersistedOutput:
        """
        Persist tool output if above threshold.

        Args:
            tool_use_id: Unique identifier for this tool call
            output: The raw tool output string

        Returns:
            PersistedOutput with preview and path info
        """
        original_size = len(output)

        if original_size <= self.config.threshold:
            return PersistedOutput(
                preview=output,
                persisted_path=None,
                was_persisted=False,
                original_size=original_size
            )

        # Persist to disk
        stored_path = self._write_to_disk(tool_use_id, output)

        # Create preview
        preview = output[:self.config.preview_chars]
        if original_size > self.config.preview_chars:
            rel_path = stored_path.relative_to(Path.cwd()) if stored_path.is_absolute() else stored_path
            preview += (
                f"\n\n... [Output persisted: {original_size:,} chars total. "
                f"Full output saved to: {rel_path}]"
            )

        # Record metadata
        self._record_metadata(tool_use_id, stored_path, original_size)

        logger.debug(f"[ToolOutputPersister] Persisted {original_size} chars to {stored_path}")

        return PersistedOutput(
            preview=preview,
            persisted_path=stored_path,
            was_persisted=True,
            original_size=original_size
        )

    def _write_to_disk(self, tool_use_id: str, output: str) -> Path:
        """Write output to disk with safe filename."""
        # Sanitize tool_use_id for filesystem safety
        safe_id = "".join(c if c.isalnum() or c in '-_' else '_' for c in tool_use_id)
        timestamp = int(time.time() * 1000)
        filename = f"{safe_id}_{timestamp}.txt"
        stored_path = self.config.storage_dir / filename

        stored_path.write_text(output, encoding="utf-8")
        return stored_path

    def _record_metadata(self, tool_use_id: str, path: Path, size: int) -> None:
        """Record metadata for later retrieval."""
        metadata = {
            "tool_use_id": tool_use_id,
            "path": str(path),
            "size": size,
            "timestamp": time.time()
        }
        with open(self._metadata_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(metadata, ensure_ascii=False) + "\n")

    def load_persisted(self, tool_use_id: str) -> Optional[str]:
        """Load a persisted output by tool_use_id (most recent match)."""
        if not self._metadata_file.exists():
            return None

        # Find most recent entry for this tool_use_id via reverse iteration
        with open(self._metadata_file, "r", encoding="utf-8") as f:
            for line in reversed(list(f)):
                try:
                    metadata = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if metadata.get("tool_use_id") == tool_use_id:
                    path = Path(metadata["path"])
                    if path.exists():
                        return path.read_text(encoding="utf-8")
        return None

    def cleanup_old(self, max_age_days: int = 7) -> int:
        """Remove persisted files older than max_age_days. Returns count removed."""
        if not self._metadata_file.exists():
            return 0

        cutoff = time.time() - (max_age_days * 86400)
        removed = 0

        # Read all metadata
        entries = []
        with open(self._metadata_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        # Filter and rewrite
        kept = []
        for entry in entries:
            if entry.get("timestamp", 0) > cutoff:
                kept.append(entry)
            else:
                path = Path(entry["path"])
                if path.exists():
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass

        # Rewrite metadata file
        with open(self._metadata_file, "w", encoding="utf-8") as f:
            for entry in kept:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        if removed > 0:
            logger.info(f"[ToolOutputPersister] Cleaned up {removed} old persisted files")

        return removed


# Module-level convenience function
_default_persister: Optional[ToolOutputPersister] = None


def get_persister() -> ToolOutputPersister:
    """Get or create the default persister instance."""
    global _default_persister
    if _default_persister is None:
        _default_persister = ToolOutputPersister()
    return _default_persister


def persist_tool_output(
    tool_use_id: str,
    output: str,
    threshold: int = 20000
) -> str:
    """
    Convenience function to persist tool output.

    Returns the preview string (original if below threshold,
    preview with path reference if persisted).

    Args:
        tool_use_id: Unique identifier for the tool call
        output: The tool output to potentially persist
        threshold: Size threshold for persistence

    Returns:
        Preview string suitable for context inclusion
    """
    persister = get_persister()
    result = persister.persist(tool_use_id, output)
    return result.preview


__all__ = [
    "ToolOutputPersister",
    "PersistedOutput",
    "PersistConfig",
    "persist_tool_output",
    "get_persister",
    "TOOL_RESULTS_DIR",
]
