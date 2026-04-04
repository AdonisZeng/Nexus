"""File patch tool for applying unified diff format patches"""
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum

from .registry import Tool
from .models import FilePatchArgs


class PatchOperationType(Enum):
    """Patch operation types"""
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class Hunk:
    """Represents a single hunk in an update operation"""
    context: list[str] = field(default_factory=list)
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)


@dataclass
class PatchOperation:
    """Represents a single patch operation"""
    op_type: PatchOperationType
    file_path: str
    content: Optional[list[str]] = None
    hunks: Optional[list[Hunk]] = None


class FilePatchTool(Tool):
    """Apply patch operations to files with atomicity guarantee"""

    @property
    def name(self) -> str:
        return "file_patch"

    @property
    def description(self) -> str:
        return (
            "Apply patch operations to files. "
            "Supports Add File, Update File (with context matching), and Delete File operations. "
            "All operations are applied atomically - either all succeed or all fail."
        )

    @property
    def is_mutating(self) -> bool:
        return True

    def _get_input_schema(self) -> dict:
        return FilePatchArgs.model_json_schema()

    async def execute(self, patch: str, **kwargs) -> str:
        """
        Execute patch operations.

        @param patch The patch text in the specified format
        @return Success message or error description
        """
        # Check protected paths
        from src.tools.protected_paths import protected_paths
        import re

        paths = re.findall(r'\*\*\*\s*(?:Add|Update|Delete)\s+File:\s*([^\n]+)', patch)
        for p in paths:
            if protected_paths.is_protected(p.strip()):
                return protected_paths.get_error_message(p.strip(), "patch")

        try:
            operations = self._parse_patch(patch)
        except ValueError as e:
            return f"Parse error: {str(e)}"

        # Validate all operations first
        validation_errors = []
        for op in operations:
            error = self._validate_operation(op)
            if error:
                validation_errors.append(f"{op.file_path}: {error}")

        if validation_errors:
            return f"Validation failed:\n" + "\n".join(validation_errors)

        # Apply operations with rollback support
        applied_operations = []
        rollback_files = {}

        try:
            for op in operations:
                self._apply_operation(op, rollback_files)
                applied_operations.append(op)

            return f"Successfully applied {len(operations)} patch operation(s)"

        except Exception as e:
            # Rollback all applied operations
            self._rollback_operations(applied_operations, rollback_files)
            return f"Application failed: {str(e)}. All changes have been rolled back."

    def _parse_patch(self, patch: str) -> list[PatchOperation]:
        """
        Parse patch text into operations.

        @param patch The patch text
        @return List of patch operations
        @raises ValueError If patch format is invalid
        """
        lines = patch.split("\n")
        operations = []
        i = 0

        # Find start marker
        while i < len(lines) and lines[i].strip() != "*** Begin Patch":
            i += 1

        if i >= len(lines):
            raise ValueError("Missing '*** Begin Patch' marker")

        i += 1  # Skip start marker

        while i < len(lines):
            line = lines[i].strip()

            if line == "*** End Patch":
                break

            if line.startswith("*** Add File:"):
                file_path = line[len("*** Add File:"):].strip()
                content_lines = []
                i += 1
                while i < len(lines) and not lines[i].startswith("***"):
                    if lines[i].startswith("+"):
                        content_lines.append(lines[i][1:])
                    elif lines[i].strip():
                        # Non-empty line without + prefix
                        raise ValueError(f"Invalid line in Add File content: {lines[i]}")
                    i += 1
                operations.append(PatchOperation(
                    op_type=PatchOperationType.ADD,
                    file_path=file_path,
                    content=content_lines
                ))
                continue

            elif line.startswith("*** Update File:"):
                file_path = line[len("*** Update File:"):].strip()
                i += 1
                hunks = []
                current_hunk = None

                while i < len(lines) and not lines[i].startswith("***"):
                    hunk_line = lines[i]

                    if hunk_line.startswith("@@"):
                        # Start new hunk
                        if current_hunk is not None:
                            hunks.append(current_hunk)
                        current_hunk = Hunk()
                        # Context line (remove @@ markers)
                        context = hunk_line[2:].strip()
                        if current_hunk.context is not None:
                            current_hunk.context.append(context)
                        i += 1
                    elif hunk_line.startswith("-"):
                        if current_hunk is None:
                            raise ValueError("Old line without hunk context")
                        current_hunk.old_lines.append(hunk_line[1:])
                        i += 1
                    elif hunk_line.startswith("+"):
                        if current_hunk is None:
                            raise ValueError("New line without hunk context")
                        current_hunk.new_lines.append(hunk_line[1:])
                        i += 1
                    elif hunk_line.strip() == "" or hunk_line.startswith(" "):
                        # Context line or empty line
                        if current_hunk is not None and hunk_line.startswith(" "):
                            current_hunk.context.append(hunk_line[1:])
                        i += 1
                    else:
                        i += 1

                if current_hunk is not None:
                    hunks.append(current_hunk)

                operations.append(PatchOperation(
                    op_type=PatchOperationType.UPDATE,
                    file_path=file_path,
                    hunks=hunks
                ))
                continue

            elif line.startswith("*** Delete File:"):
                file_path = line[len("*** Delete File:"):].strip()
                operations.append(PatchOperation(
                    op_type=PatchOperationType.DELETE,
                    file_path=file_path
                ))
                i += 1
                continue

            else:
                i += 1

        return operations

    def _validate_operation(self, op: PatchOperation) -> Optional[str]:
        """
        Validate a single patch operation.

        @param op The operation to validate
        @return Error message if validation fails, None if successful
        """
        path = Path(op.file_path)

        if op.op_type == PatchOperationType.ADD:
            if path.exists():
                return f"File already exists: {op.file_path}"
            return None

        elif op.op_type == PatchOperationType.UPDATE:
            if not path.exists():
                return f"File does not exist: {op.file_path}"

            try:
                content = path.read_text(encoding="utf-8")
                lines = content.split("\n")
            except Exception as e:
                return f"Cannot read file: {str(e)}"

            # Validate each hunk's context
            for hunk_idx, hunk in enumerate(op.hunks or []):
                context_match = self._find_context(lines, hunk)
                if context_match < 0:
                    return f"Hunk {hunk_idx + 1}: Context does not match"

                # Verify old lines exist at the context location
                old_lines = hunk.old_lines
                if context_match >= 0 and old_lines:
                    # Calculate where old lines should be
                    context_end = context_match + len(hunk.context)
                    for j, old_line in enumerate(old_lines):
                        if context_end + j >= len(lines):
                            return f"Hunk {hunk_idx + 1}: Old line {j + 1} exceeds file length"
                        if lines[context_end + j] != old_line:
                            return (
                                f"Hunk {hunk_idx + 1}: Old line {j + 1} does not match. "
                                f"Expected: {old_line!r}, Found: {lines[context_end + j]!r}"
                            )

            return None

        elif op.op_type == PatchOperationType.DELETE:
            if not path.exists():
                return f"File does not exist: {op.file_path}"
            return None

        return None

    def _find_context(self, lines: list[str], hunk: Hunk) -> int:
        """
        Find the context in the file lines.

        @param lines File lines
        @param hunk The hunk to find context for
        @return Index of context start, or -1 if not found
        """
        context = hunk.context
        if not context:
            return 0

        # Try to find the context in the file
        for i in range(len(lines) - len(context) + 1):
            match = True
            for j, ctx_line in enumerate(context):
                if i + j >= len(lines) or lines[i + j] != ctx_line:
                    match = False
                    break
            if match:
                return i

        return -1

    def _apply_operation(
        self,
        op: PatchOperation,
        rollback_files: dict
    ) -> None:
        """
        Apply a single operation.

        @param op The operation to apply
        @param rollback_files Dictionary to store rollback information
        @raises Exception If application fails
        """
        path = Path(op.file_path)

        if op.op_type == PatchOperationType.ADD:
            # Store for rollback (file didn't exist)
            rollback_files[op.file_path] = None

            # Create parent directories
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            content = "\n".join(op.content or [])
            path.write_text(content, encoding="utf-8")

        elif op.op_type == PatchOperationType.UPDATE:
            # Store original content for rollback
            if op.file_path not in rollback_files:
                rollback_files[op.file_path] = path.read_text(encoding="utf-8")

            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
            new_lines = []
            i = 0

            # Sort hunks by context position (apply from end to start to maintain indices)
            sorted_hunks = sorted(
                enumerate(op.hunks or []),
                key=lambda x: self._find_context(lines, x[1]),
                reverse=True
            )

            for hunk_idx, hunk in sorted_hunks:
                context_start = self._find_context(lines, hunk)
                if context_start < 0:
                    raise ValueError(f"Context mismatch in hunk {hunk_idx + 1}")

                context_end = context_start + len(hunk.context)
                old_lines_count = len(hunk.old_lines)

                # Build new lines
                before = lines[:context_end]
                after = lines[context_end + old_lines_count:]
                replacement = hunk.new_lines

                lines = before + replacement + after

            # Write updated content
            new_content = "\n".join(lines)
            path.write_text(new_content, encoding="utf-8")

        elif op.op_type == PatchOperationType.DELETE:
            # Store original content for rollback
            rollback_files[op.file_path] = path.read_text(encoding="utf-8")

            # Delete the file
            path.unlink()

    def _rollback_operations(
        self,
        operations: list[PatchOperation],
        rollback_files: dict
    ) -> None:
        """
        Rollback applied operations.

        @param operations List of operations that were applied
        @param rollback_files Dictionary with rollback information
        """
        for op in reversed(operations):
            path = Path(op.file_path)
            original_content = rollback_files.get(op.file_path)

            try:
                if op.op_type == PatchOperationType.ADD:
                    # Delete the created file
                    if path.exists():
                        path.unlink()
                        # Try to remove empty parent directories
                        try:
                            parent = path.parent
                            while parent != parent.parent:
                                if not any(parent.iterdir()):
                                    parent.rmdir()
                                parent = parent.parent
                        except OSError:
                            pass

                elif op.op_type == PatchOperationType.UPDATE:
                    # Restore original content
                    if original_content is not None:
                        path.write_text(original_content, encoding="utf-8")

                elif op.op_type == PatchOperationType.DELETE:
                    # Restore deleted file
                    if original_content is not None:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(original_content, encoding="utf-8")

            except Exception:
                # Best effort rollback - ignore errors
                pass
