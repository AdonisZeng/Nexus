"""Directory listing tool"""
import asyncio
from collections import deque
from pathlib import Path
from typing import Any

from .registry import Tool
from .models import ListDirArgs


class ListDirTool(Tool):
    """List directory contents with recursive traversal"""

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List directory contents. Supports recursive traversal with depth limit, pagination with offset and limit. Input: dir_path (string, required), depth (int, optional), offset (int, optional), limit (int, optional)"

    @property
    def is_mutating(self) -> bool:
        return False

    async def execute(
        self,
        dir_path: str,
        offset: int = 1,
        limit: int = 25,
        depth: int = 2,
        **kwargs
    ) -> str:
        """
        List directory contents.

        @param dir_path The directory path to list
        @param offset Starting entry index (1-based)
        @param limit Maximum number of entries to return
        @param depth Maximum recursion depth for subdirectories
        @return Formatted directory listing
        """
        path = Path(dir_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")
        if not path.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {dir_path}")

        # BFS traversal using queue: (path, current_depth, indent_level)
        queue = deque([(path, 1, 0)])
        all_entries = []

        loop = asyncio.get_event_loop()

        while queue:
            current_path, current_depth, indent_level = queue.popleft()

            try:
                # Get directory entries asynchronously
                entries = await loop.run_in_executor(
                    None,
                    lambda: sorted(
                        current_path.iterdir(),
                        key=lambda x: (not x.is_dir(), x.name.lower())
                    )
                )

                for entry in entries:
                    # Determine entry type marker
                    marker = ""
                    try:
                        if entry.is_symlink():
                            marker = "@"
                        elif entry.is_dir():
                            marker = "/"
                    except (OSError, PermissionError):
                        pass

                    all_entries.append((entry, indent_level, marker))

                    # Add subdirectories to queue for BFS traversal
                    if current_depth < depth:
                        try:
                            if entry.is_dir() and not entry.is_symlink():
                                queue.append((entry, current_depth + 1, indent_level + 1))
                        except (OSError, PermissionError):
                            pass

            except (OSError, PermissionError):
                # Skip directories we can't read
                continue

        # Apply pagination (1-based offset)
        start_idx = max(0, offset - 1)
        end_idx = start_idx + limit
        paginated_entries = all_entries[start_idx:end_idx]

        # Format output
        lines = [f"Absolute path: {path}"]

        for entry, indent_level, marker in paginated_entries:
            indent = "  " * indent_level
            name = entry.name + marker
            lines.append(f"{indent}{name}")

        # Add pagination info if needed
        if len(all_entries) > limit:
            lines.append(f"\n... ({len(all_entries) - start_idx - len(paginated_entries)} more entries)")

        return "\n".join(lines)

    def get_schema(self, input_model: type[Any] | None = None) -> dict:
        """
        Get tool schema with ListDirArgs model.

        @param input_model Optional Pydantic model (defaults to ListDirArgs)
        @return Tool schema dictionary
        """
        return super().get_schema(input_model or ListDirArgs)
