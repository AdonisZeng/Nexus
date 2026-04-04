"""File operation tools"""
import os
import asyncio
from pathlib import Path
from typing import Any, Optional
from .registry import Tool
from .models import FileReadArgs, SearchArgs


class FileReadTool(Tool):
    """Read file contents"""

    is_mutating = False

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read the contents of a file with pagination and indentation-aware mode support"

    def _get_indent_level(self, line: str) -> int:
        """计算行的缩进级别（空格数）"""
        return len(line) - len(line.lstrip())

    def _format_line(self, line_number: int, content: str, max_length: int = 500) -> str:
        """格式化单行输出，限制字符数"""
        if len(content) > max_length:
            content = content[:max_length] + "..."
        return f"L{line_number}: {content}"

    def _read_slice_mode(self, lines: list[str], offset: int, limit: int) -> list[str]:
        """切片模式：按行号范围读取"""
        start_idx = max(0, offset - 1)
        end_idx = min(len(lines), start_idx + limit)
        result = []
        for i in range(start_idx, end_idx):
            result.append(self._format_line(i + 1, lines[i]))
        return result

    def _read_indentation_mode(self, lines: list[str], anchor_line: int) -> list[str]:
        """缩进感知模式：以锚定行为中心，收集相同或更深缩进级别的行"""
        if anchor_line < 1 or anchor_line > len(lines):
            return [f"L0: Error: anchor_line {anchor_line} is out of range (1-{len(lines)})"]

        anchor_idx = anchor_line - 1
        anchor_indent = self._get_indent_level(lines[anchor_idx])

        # 向上扩展
        start_idx = anchor_idx
        for i in range(anchor_idx - 1, -1, -1):
            current_indent = self._get_indent_level(lines[i])
            if current_indent < anchor_indent:
                break
            start_idx = i

        # 向下扩展
        end_idx = anchor_idx
        for i in range(anchor_idx + 1, len(lines)):
            current_indent = self._get_indent_level(lines[i])
            if current_indent < anchor_indent:
                break
            end_idx = i

        result = []
        for i in range(start_idx, end_idx + 1):
            result.append(self._format_line(i + 1, lines[i]))
        return result

    async def execute(
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
        mode: str = "slice",
        anchor_line: Optional[int] = None,
        **kwargs
    ) -> str:
        """Read file contents with pagination and mode support"""
        # 使用 Pydantic 模型验证参数
        args = FileReadArgs(
            file_path=file_path,
            offset=offset,
            limit=limit,
            mode=mode,
            anchor_line=anchor_line
        )

        path = Path(args.file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {args.file_path}")

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(
            None,
            lambda: path.read_text(encoding="utf-8")
        )

        lines = content.splitlines()

        if args.mode == "slice":
            result_lines = self._read_slice_mode(lines, args.offset, args.limit)
        elif args.mode == "indentation":
            if args.anchor_line is None:
                return "L0: Error: anchor_line is required for 'indentation' mode"
            result_lines = self._read_indentation_mode(lines, args.anchor_line)
        else:
            return f"L0: Error: Unknown mode '{args.mode}'. Supported modes: 'slice', 'indentation'"

        return "\n".join(result_lines)

    def _get_input_schema(self) -> dict:
        return FileReadArgs.model_json_schema()


class FileWriteTool(Tool):
    """Write content to a file"""

    is_mutating = True

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "[Legacy] Write content to a file. Recommended: use file_patch instead. Creates the file if it doesn't exist. Input: file_path (string, required), content (string, required)"

    async def execute(self, file_path: str, content: str, **kwargs) -> str:
        """Write content to file"""
        from src.tools.protected_paths import protected_paths

        if protected_paths.is_protected(file_path):
            worktree_path = protected_paths.get_current_worktree_path()
            return protected_paths.get_error_message(file_path, "write", worktree_path)

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: path.write_text(content, encoding="utf-8")
        )
        return f"Successfully wrote to {file_path}"

    def _get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                }
            },
            "required": ["file_path", "content"]
        }


class FileSearchTool(Tool):
    """Search for files matching a pattern"""

    is_mutating = False

    @property
    def name(self) -> str:
        return "file_search"

    @property
    def description(self) -> str:
        return "Search for files matching a pattern. Input: pattern (string, required) - regex pattern to search, path (string, optional) - directory to search, include (string, optional) - glob filter (e.g., *.py), limit (int, optional) - max results (default 100, max 2000)"

    def _is_ripgrep_available(self) -> bool:
        """检测 ripgrep 是否已安装"""
        try:
            import subprocess
            subprocess.run(
                ["rg", "--version"],
                capture_output=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    async def _search_with_ripgrep(
        self,
        pattern: str,
        path: str,
        include: Optional[str],
        limit: int
    ) -> list[str]:
        """使用 ripgrep 搜索文件"""
        import subprocess

        args = ["rg", "--files-with-matches", "--sortr=modified"]
        if include:
            args.extend(["--glob", include])
        args.extend(["--", pattern, path])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        stdout, _ = await proc.communicate()

        results = stdout.decode("utf-8", errors="ignore").strip().split("\n")
        results = [r for r in results if r]
        return results[:limit]

    async def _search_with_python(
        self,
        pattern: str,
        path: str,
        include: Optional[str],
        limit: int
    ) -> list[str]:
        """使用 Python 实现搜索文件（回退方案）"""
        import fnmatch
        import re

        search_path = Path(path)
        results = []

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = None

        for file_path in search_path.rglob("*"):
            if not file_path.is_file():
                continue

            if include and not fnmatch.fnmatch(file_path.name, include):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                if regex:
                    if regex.search(content):
                        results.append(str(file_path))
                else:
                    if pattern.lower() in content.lower():
                        results.append(str(file_path))

                if len(results) >= limit:
                    break
            except Exception:
                pass

        return results

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        include: Optional[str] = None,
        limit: int = 100,
        **kwargs
    ) -> str:
        """Search for files matching a pattern"""
        args = SearchArgs(
            pattern=pattern,
            path=path,
            include=include,
            limit=limit
        )

        search_path = Path(args.path)
        if not search_path.exists():
            raise FileNotFoundError(f"Path not found: {args.path}")

        if self._is_ripgrep_available():
            results = await self._search_with_ripgrep(
                args.pattern,
                str(search_path),
                args.include,
                args.limit
            )
        else:
            results = await self._search_with_python(
                args.pattern,
                str(search_path),
                args.include,
                args.limit
            )

        if not results:
            return "No files found"

        return "\n".join(results)

    def _get_input_schema(self) -> dict:
        return SearchArgs.model_json_schema()