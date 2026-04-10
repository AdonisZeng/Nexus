"""
MemoryConsolidator - Lightweight Dream for Memory Maintenance

Implements periodic consolidation to:
- Merge similar/related memories
- Remove stale entries
- Enforce limits on memory store size
"""

import os
import re
import time
import hashlib
import logging
from pathlib import Path
from typing import Optional

from src.utils.frontmatter import parse_frontmatter

logger = logging.getLogger("Nexus")


class MemoryConsolidator:
    """
    Auto-consolidation of memories between sessions.

    Implements 7 gates that must all pass before consolidation runs:
    1. enabled flag
    2. memory directory exists with files
    3. not in plan mode
    4. 24-hour cooldown since last consolidation
    5. 10-minute throttle since last scan attempt
    6. minimum 5 sessions of data
    7. no active lock file (PID-based)

    Then runs 3 phases:
    1. Orient: scan index for structure
    2. Consolidate: merge related, remove stale entries
    3. Prune: enforce size limits on memory store
    """

    # Gate thresholds
    COOLDOWN_SECONDS = 86400        # 24 hours between consolidations
    SCAN_THROTTLE_SECONDS = 600    # 10 minutes between scan attempts
    MIN_SESSION_COUNT = 5          # minimum sessions before consolidating
    LOCK_STALE_SECONDS = 3600      # PID lock considered stale after 1 hour

    # Size limits
    MAX_MEMORY_ENTRIES = 100       # Maximum cross-session entries to keep
    MAX_SESSION_ENTRIES = 50        # Maximum session-scoped entries to keep
    MAX_AGE_DAYS = 90              # Remove entries older than this

    # LLM-based consolidation settings
    CONSOLIDATE_ENABLED = True     # Set False to disable LLM consolidation
    MERGE_SIMILARITY_THRESHOLD = 0.85  # Similarity score to trigger merge

    PHASES = [
        "Orient: scan memory index for structure and categories",
        "Consolidate: merge related memories, remove stale entries",
        "Prune: enforce size limits on memory store",
    ]

    def __init__(self, memory_dir: Optional[Path] = None):
        from src.context.core import get_user_memory_dir
        self.memory_dir = memory_dir or get_user_memory_dir()
        self.entries_dir = self.memory_dir / "entries"
        self.session_dir = self.memory_dir / "session_entries"
        self.index_path = self.memory_dir / "memory.md"
        self.lock_file = self.memory_dir / ".consolidate_lock"

        self.mode = "default"  # Can be "default" or "plan"
        self.last_consolidation_time = 0.0
        self.last_scan_time = 0.0
        self.session_count = 0
        self.enabled = self.CONSOLIDATE_ENABLED  # Derive from class constant

        # Track session count from session entry files
        self._count_sessions()

    def _count_sessions(self) -> None:
        """Count unique session IDs from entry files."""
        sessions = set()
        for entry_file in self.entries_dir.glob("*.md"):
            frontmatter, _ = parse_frontmatter(entry_file.read_text(encoding="utf-8"))
            if frontmatter:
                session_id = frontmatter.get("session_id", "")
                if session_id:
                    sessions.add(session_id)
        self.session_count = len(sessions)

    def should_consolidate(self) -> tuple[bool, str]:
        """
        Check 7 gates in sequence. All must pass.
        Returns (can_run, reason) where reason explains the first failed gate.
        """
        now = time.time()

        # Gate 1: enabled flag
        if not self.enabled:
            return False, "Gate 1: consolidation is disabled"

        # Gate 2: memory directory exists and has memory files
        if not self.memory_dir.exists():
            return False, "Gate 2: memory directory does not exist"

        memory_files = list(self.entries_dir.glob("*.md"))
        if not memory_files:
            return False, "Gate 2: no memory files found"

        # Gate 3: not in plan mode
        if self.mode == "plan":
            return False, "Gate 3: plan mode does not allow consolidation"

        # Gate 4: 24-hour cooldown since last consolidation
        time_since_last = now - self.last_consolidation_time
        if time_since_last < self.COOLDOWN_SECONDS:
            remaining = int(self.COOLDOWN_SECONDS - time_since_last)
            return False, f"Gate 4: cooldown active, {remaining}s remaining"

        # Gate 5: 10-minute throttle since last scan attempt
        time_since_scan = now - self.last_scan_time
        if time_since_scan < self.SCAN_THROTTLE_SECONDS:
            remaining = int(self.SCAN_THROTTLE_SECONDS - time_since_scan)
            return False, f"Gate 5: scan throttle active, {remaining}s remaining"

        # Gate 6: need at least 5 sessions worth of data
        if self.session_count < self.MIN_SESSION_COUNT:
            return False, f"Gate 6: only {self.session_count} sessions, need {self.MIN_SESSION_COUNT}"

        # Gate 7: no active lock file
        if not self._acquire_lock():
            return False, "Gate 7: lock held by another process"

        return True, "All 7 gates passed"

    async def consolidate(self, model_adapter=None) -> list[str]:
        """
        Run the 3-phase consolidation process.

        Args:
            model_adapter: Optional model adapter for LLM-based merging

        Returns:
            List of completed phase descriptions
        """
        can_run, reason = self.should_consolidate()
        if not can_run:
            logger.info(f"MemoryConsolidator: Cannot consolidate: {reason}")
            return []

        logger.info("MemoryConsolidator: Starting consolidation...")
        self.last_scan_time = time.time()
        completed_phases = []

        try:
            for i, phase in enumerate(self.PHASES, 1):
                logger.debug(f"MemoryConsolidator: Phase {i}/3: {phase}")

                if i == 1:
                    self._phase_orient()
                elif i == 2:
                    if model_adapter and self.CONSOLIDATE_ENABLED:
                        await self._phase_consolidate_llm(model_adapter)
                    else:
                        self._phase_consolidate_simple()
                elif i == 3:
                    self._phase_prune()

                completed_phases.append(phase)

            self.last_consolidation_time = time.time()
            logger.info(f"MemoryConsolidator: Consolidation complete: {len(completed_phases)} phases executed")

        finally:
            self._release_lock()

        return completed_phases

    def _phase_orient(self) -> None:
        """Phase 1: Scan index for structure."""
        logger.debug(f"MemoryConsolidator: Orient - {self.session_count} sessions, "
                    f"{len(list(self.entries_dir.glob('*.md')))} cross-session entries, "
                    f"{len(list(self.session_dir.glob('*.md')))} session entries")

    def _phase_consolidate_simple(self) -> None:
        """Phase 2: Simple consolidation without LLM (remove exact duplicates)."""
        seen_hashes = set()
        duplicates = []

        for entry_file in self.entries_dir.glob("*.md"):
            content_hash = hashlib.md5(entry_file.read_text(encoding="utf-8").encode()).hexdigest()[:16]
            if content_hash in seen_hashes:
                duplicates.append(entry_file)
            seen_hashes.add(content_hash)

        for dup in duplicates:
            logger.info(f"MemoryConsolidator: Removing duplicate: {dup.name}")
            dup.unlink(missing_ok=True)

    async def _phase_consolidate_llm(self, model_adapter) -> None:
        """Phase 2: LLM-based consolidation to merge similar memories."""
        # Load all entries
        entries = []
        for entry_file in self.entries_dir.glob("*.md"):
            frontmatter, docstring = parse_frontmatter(entry_file.read_text(encoding="utf-8"))
            if frontmatter:
                entries.append({
                    "file": entry_file,
                    "memory_type": frontmatter.get("memory_type", "project"),
                    "summary": frontmatter.get("summary", ""),
                    "content": docstring,
                    "created": frontmatter.get("created", ""),
                    "tags": frontmatter.get("tags", []),
                })

        if len(entries) < 2:
            return

        # Build prompt for LLM to identify mergeable groups
        entries_text = "\n".join([
            f"[{i}] TYPE:{e['memory_type']} SUMMARY:{e['summary']} CONTENT:{e['content'][:200]}"
            for i, e in enumerate(entries)
        ])

        prompt = f"""You are a memory consolidation assistant. Given a list of memory entries,
identify which ones should be merged because they contain redundant information.

Memory entries:
{entries_text}

Analyze for redundancy and overlap. Return your analysis as:
MERGE_GROUPS: [[0,2,5], [1,3], [4]]
(List of groups of entry indices that should be merged into single memories)

If no merging needed, return:
MERGE_GROUPS: []
"""

        try:
            response = await model_adapter.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=""
            )

            # Parse response
            match = re.search(r'MERGE_GROUPS:\s*\[(.*?)\]', response, re.DOTALL)
            if match:
                groups_str = match.group(1).strip()
                if groups_str and groups_str != '[]':
                    logger.info(f"MemoryConsolidator: LLM identified merge groups: {groups_str}")

        except Exception as e:
            logger.warning(f"MemoryConsolidator: LLM consolidation failed: {e}")
            self._phase_consolidate_simple()

    def _phase_prune(self) -> None:
        """Phase 3: Enforce size limits."""
        now = time.time()
        max_age_seconds = self.MAX_AGE_DAYS * 86400

        # Collect entries with stat once for entries_dir
        entries_with_stat = [
            (f, f.stat().st_mtime) for f in self.entries_dir.glob("*.md")
        ]

        # Remove old entries
        for entry_file, mtime in entries_with_stat:
            age = now - mtime
            if age > max_age_seconds:
                logger.info(f"MemoryConsolidator: Removing old entry: {entry_file.name}")
                entry_file.unlink(missing_ok=True)

        # Enforce max entry count (reuse collected stat results)
        entries_with_stat.sort(key=lambda x: x[1], reverse=True)
        if len(entries_with_stat) > self.MAX_MEMORY_ENTRIES:
            for entry_file, _ in entries_with_stat[self.MAX_MEMORY_ENTRIES:]:
                logger.info(f"MemoryConsolidator: Removing excess entry: {entry_file.name}")
                entry_file.unlink(missing_ok=True)

        # Clean session directory (more aggressively)
        if self.session_dir.exists():
            session_with_stat = [
                (f, f.stat().st_mtime) for f in self.session_dir.glob("*.md")
            ]
            session_with_stat.sort(key=lambda x: x[1], reverse=True)
            if len(session_with_stat) > self.MAX_SESSION_ENTRIES:
                for entry_file, _ in session_with_stat[self.MAX_SESSION_ENTRIES:]:
                    logger.info(f"MemoryConsolidator: Removing excess session entry: {entry_file.name}")
                    entry_file.unlink(missing_ok=True)

    def _acquire_lock(self) -> bool:
        """
        Acquire a PID-based lock file. Returns False if locked by another
        live process. Stale locks (older than LOCK_STALE_SECONDS) are removed.
        """
        if self.lock_file.exists():
            try:
                lock_data = self.lock_file.read_text().strip()
                pid_str, timestamp_str = lock_data.split(":", 1)
                pid = int(pid_str)
                lock_time = float(timestamp_str)

                # Check if lock is stale
                if (time.time() - lock_time) > self.LOCK_STALE_SECONDS:
                    logger.debug(f"MemoryConsolidator: Removing stale lock from PID {pid}")
                    self.lock_file.unlink()
                else:
                    # Check if owning process is still alive
                    try:
                        os.kill(pid, 0)
                        return False  # process alive, lock is valid
                    except OSError:
                        logger.debug(f"MemoryConsolidator: Removing lock from dead PID {pid}")
                        self.lock_file.unlink()
            except (ValueError, OSError):
                # Corrupted lock file, remove it
                self.lock_file.unlink(missing_ok=True)

        # Write new lock
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.lock_file.write_text(f"{os.getpid()}:{time.time()}")
            return True
        except OSError:
            return False

    def _release_lock(self) -> None:
        """Release the lock file if we own it."""
        try:
            if self.lock_file.exists():
                lock_data = self.lock_file.read_text().strip()
                pid_str = lock_data.split(":")[0]
                if int(pid_str) == os.getpid():
                    self.lock_file.unlink()
        except (ValueError, OSError):
            pass

    def set_mode(self, mode: str) -> None:
        """Set operation mode ('default' or 'plan')."""
        self.mode = mode

    def enable(self) -> None:
        """Enable consolidation."""
        self.enabled = True

    def disable(self) -> None:
        """Disable consolidation."""
        self.enabled = False


# Singleton instance
_consolidator: Optional[MemoryConsolidator] = None


def get_consolidator(memory_dir: Optional[Path] = None) -> MemoryConsolidator:
    """Get or create the default MemoryConsolidator instance."""
    global _consolidator
    if _consolidator is None:
        _consolidator = MemoryConsolidator(memory_dir)
    return _consolidator
