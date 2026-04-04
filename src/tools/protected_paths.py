"""Protected paths registry for team work_roots"""
from pathlib import Path
from typing import Optional, Set


class ProtectedPathsRegistry:
    """Global registry of protected paths that should not be directly written"""

    _instance = None
    _protected_paths: Set[str] = set()  # work_root 等需要保护的路径
    _exempt_paths: Set[str] = set()  # 豁免路径（如 Lead 的 SPEC 文件）
    _current_worktree_path: Optional[str] = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_current_worktree_path(self, path: str) -> None:
        """Set the current member's worktree path for error messages"""
        self._current_worktree_path = path

    def get_current_worktree_path(self) -> Optional[str]:
        """Get the current member's worktree path"""
        return self._current_worktree_path

    def clear_current_worktree_path(self) -> None:
        """Clear the current worktree path"""
        self._current_worktree_path = None

    def add_protected_path(self, path: str) -> None:
        """Add a path to protected set (e.g., work_root)"""
        resolved = str(Path(path).resolve())
        self._protected_paths.add(resolved)

    def remove_protected_path(self, path: str) -> None:
        """Remove a path from protected set"""
        resolved = str(Path(path).resolve())
        self._protected_paths.discard(resolved)

    def add_exempt_path(self, path: str) -> None:
        """Add a path to exempt set (e.g., SPEC file for Lead to write)"""
        resolved = str(Path(path).resolve())
        self._exempt_paths.add(resolved)

    def remove_exempt_path(self, path: str) -> None:
        """Remove a path from exempt set"""
        resolved = str(Path(path).resolve())
        self._exempt_paths.discard(resolved)

    def is_protected(self, path: str) -> bool:
        """Check if a path is protected.

        Allows:
        - Paths in exempt set (e.g., SPEC file for Lead)
        - Paths inside the current member's own worktree (member can write files there)

        Blocks:
        - Paths inside work_root (not a member's worktree and not exempt)
        - Paths that are not inside any member's worktree
        """
        try:
            resolved = Path(path).resolve()

            # 1. 豁免路径检查 - 如果路径在豁免路径中，允许
            for exempt in self._exempt_paths:
                if str(resolved).startswith(exempt):
                    return False

            # 2. 如果路径在当前成员的 worktree 中，允许写入
            if self._current_worktree_path:
                current_wt = Path(self._current_worktree_path).resolve()
                try:
                    resolved.relative_to(current_wt)
                    # 路径在当前成员的 worktree 中，允许
                    return False
                except ValueError:
                    # 路径不在当前成员的 worktree 中，继续检查
                    pass

            # 3. 检查是否是受保护的 work_root 路径
            for protected in self._protected_paths:
                protected_path = Path(protected)
                try:
                    relative = resolved.relative_to(protected_path)
                    # Block if exactly equal (len=0) or direct child (len=1)
                    if len(relative.parts) <= 1:
                        return True
                except ValueError:
                    pass
        except Exception:
            pass
        return False

    def get_error_message(self, path: str, operation: str = "write", worktree_path: Optional[str] = None) -> str:
        if worktree_path:
            return (
                f"Error: Cannot {operation} to '{path}'.\n"
                f"此路径是团队 work_root，已被保护。\n\n"
                f"你的 worktree 路径是：{worktree_path}\n"
                f"请将文件写入你的 worktree 目录。\n"
                f"正确示例：{worktree_path}/game-logic.js"
            )
        return (
            f"Error: Cannot {operation} to '{path}'.\n"
            f"This path is a team work_root and is protected.\n"
            f"Code files must be created by team members in their worktrees,\n"
            f"then merged using complete_task.\n"
            f"Do not directly write files when working with a team."
        )


# Global instance
protected_paths = ProtectedPathsRegistry.get_instance()
