"""Worktree manager module - Git worktree lifecycle management for team tasks"""
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from .event_bus import EventBus
from src.utils import get_logger

logger = get_logger("team.worktree_manager")

DANGEROUS_COMMANDS = frozenset({
    "rm", "rm -rf", "del", "deltree", "format", "mkfs",
    "dd", "fdisk", "parted",
})


class WorktreeManager:
    """Git worktree lifecycle manager for team development"""

    BASE_DIR = Path.home() / ".nexus" / "teams"

    def __init__(self, team_name: str, event_bus: EventBus):
        """
        @brief Initialize WorktreeManager

        @param team_name Name of the team
        @param event_bus EventBus instance for logging events
        """
        self._team_name = team_name
        self._event_bus = event_bus
        self._worktrees_dir = self.BASE_DIR / team_name / "worktrees"
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._worktrees_dir / "index.json"
        self._git_checked = False
        self._git_available = False

    def _check_git(self) -> bool:
        """
        @brief Check if current directory is inside a git repository

        @return True if inside git repo, False otherwise
        """
        if self._git_checked:
            return self._git_available

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
            )
            self._git_available = result.returncode == 0 and "true" in result.stdout.lower()
            self._git_checked = True
            if not self._git_available:
                logger.warning(f"[WorktreeManager] Git not available in {self._worktrees_dir}: returncode={result.returncode}, stdout={result.stdout.strip()}")
            return self._git_available
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
            self._git_available = False
            self._git_checked = True
            logger.warning(f"[WorktreeManager] Git check failed in {self._worktrees_dir}: {e}")
            return False

    def is_available(self) -> bool:
        """
        @brief Check if worktree functionality is available

        @return True if git is available and inside a git repo
        """
        return self._check_git()

    def _validate_name(self, name: str) -> tuple[bool, str]:
        """
        @brief Validate worktree name format

        @param name Worktree name to validate
        @return Tuple of (is_valid, error_message)
        """
        if not name:
            return False, "Worktree name cannot be empty"

        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return False, "Worktree name can only contain letters, numbers, hyphens and underscores"

        if len(name) > 64:
            return False, "Worktree name cannot exceed 64 characters"

        reserved = {"new", "head", "delete", "list", "prune"}
        if name.lower() in reserved:
            return False, f"'{name}' is a reserved name"

        return True, ""

    def _ensure_index(self) -> dict:
        """
        @brief Ensure index.json exists with default structure

        @return Index data dictionary
        """
        if not self._index_path.exists():
            index_data = {
                "version": 1,
                "worktrees": {},
                "updated_at": time.time(),
            }
            self._save_index(index_data)
        return self._load_index()

    def _load_index(self) -> dict:
        """
        @brief Load index.json data

        @return Index data dictionary
        """
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return self._ensure_index()

    def _save_index(self, data: dict) -> None:
        """
        @brief Save data to index.json

        @param data Index data to save
        """
        data["updated_at"] = time.time()
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _run_git(self, args: list[str]) -> tuple[int, str, str]:
        """
        @brief Execute git command

        @param args Git command arguments
        @return Tuple of (return_code, stdout, stderr)
        """
        try:
            result = subprocess.run(
                ["git"] + args,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "Git command timed out"
        except FileNotFoundError:
            return -1, "", "Git command not found"
        except subprocess.SubprocessError as e:
            return -1, "", str(e)

    def create(
        self,
        name: str,
        task_id: Optional[int] = None,
        base_ref: str = "HEAD",
    ) -> tuple[bool, str]:
        """
        @brief Create a new git worktree

        @param name Worktree name
        @param task_id Optional task ID to bind
        @param base_ref Base git reference (default: HEAD)
        @return Tuple of (success, message)
        """
        logger.info(f"[WorktreeManager] Attempting to create worktree '{name}' (base_ref={base_ref}, task_id={task_id})")

        if not self._check_git():
            logger.warning(f"[WorktreeManager] Cannot create worktree '{name}': not inside a git repository")
            return False, "Not inside a git repository. Worktree creation requires a git repo."

        is_valid, error_msg = self._validate_name(name)
        if not is_valid:
            logger.warning(f"[WorktreeManager] Cannot create worktree '{name}': validation failed: {error_msg}")
            return False, error_msg

        index = self._ensure_index()

        if name in index.get("worktrees", {}):
            logger.warning(f"[WorktreeManager] Cannot create worktree '{name}': already exists")
            return False, f"Worktree '{name}' already exists"

        worktree_path = self._worktrees_dir / name

        if worktree_path.exists() and any(worktree_path.iterdir()):
            logger.warning(f"[WorktreeManager] Cannot create worktree '{name}': directory {worktree_path} already exists and is not empty")
            return False, f"Directory {worktree_path} already exists and is not empty"

        worktree_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"[WorktreeManager] Created directory {worktree_path}, running git worktree add --force {worktree_path} {base_ref}")

        code, stdout, stderr = self._run_git([
            "worktree", "add",
            "--force",
            worktree_path.as_posix(),
            base_ref,
        ])

        if code != 0:
            import shutil
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
            return False, f"Failed to create worktree: {stderr or stdout}"

        worktree_info = {
            "name": name,
            "path": str(worktree_path),
            "base_ref": base_ref,
            "task_id": task_id,
            "created_at": time.time(),
            "removed_at": None,
        }

        index["worktrees"][name] = worktree_info
        self._save_index(index)

        self._event_bus.emit(
            event_type="worktree_created",
            task_id=task_id,
            worktree_name=name,
            worktree_path=str(worktree_path),
            base_ref=base_ref,
        )

        logger.info(f"[WorktreeManager] SUCCESS: Created worktree '{name}' at {worktree_path} (base_ref={base_ref}, task_id={task_id})")
        return True, f"Worktree '{name}' created at {worktree_path}"

    def bind_task(self, name: str, task_id: int) -> tuple[bool, str]:
        """
        @brief Bind a task to an existing worktree

        @param name Worktree name
        @param task_id Task ID to bind
        @return Tuple of (success, message)
        """
        index = self._ensure_index()

        if name not in index.get("worktrees", {}):
            return False, f"Worktree '{name}' does not exist"

        if index["worktrees"][name].get("removed_at"):
            return False, f"Worktree '{name}' has been removed"

        index["worktrees"][name]["task_id"] = task_id
        self._save_index(index)

        self._event_bus.emit(
            event_type="worktree_task_bound",
            task_id=task_id,
            worktree_name=name,
        )

        return True, f"Task {task_id} bound to worktree '{name}'"

    def unbind_task(self, name: str) -> tuple[bool, str]:
        """
        @brief Unbind task from a worktree

        @param name Worktree name
        @return Tuple of (success, message)
        """
        index = self._ensure_index()

        if name not in index.get("worktrees", {}):
            return False, f"Worktree '{name}' does not exist"

        old_task_id = index["worktrees"][name].get("task_id")
        index["worktrees"][name]["task_id"] = None
        self._save_index(index)

        self._event_bus.emit(
            event_type="worktree_task_unbound",
            task_id=None,
            worktree_name=name,
            previous_task_id=old_task_id,
        )

        return True, f"Task unbound from worktree '{name}'"

    def run(self, name: str, command: str) -> tuple[bool, str]:
        """
        @brief Execute a command in a worktree

        @param name Worktree name
        @param command Command to execute
        @return Tuple of (success, output)
        """
        index = self._ensure_index()

        if name not in index.get("worktrees", {}):
            return False, f"Worktree '{name}' does not exist"

        if index["worktrees"][name].get("removed_at"):
            return False, f"Worktree '{name}' has been removed"

        worktree_path = Path(index["worktrees"][name]["path"])

        if not worktree_path.exists():
            return False, f"Worktree path {worktree_path} does not exist"

        cmd_lower = command.strip().split()[0].lower() if command.strip() else ""
        if cmd_lower in DANGEROUS_COMMANDS:
            return False, f"Command '{cmd_lower}' is not allowed for safety reasons"

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(worktree_path),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=300,
            )
            output = result.stdout + ("\n" + result.stderr if result.stderr else "")
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "Command execution timed out (300 seconds)"
        except subprocess.SubprocessError as e:
            return False, f"Command execution failed: {str(e)}"

    def remove(self, name: str, force: bool = False) -> tuple[bool, str]:
        """
        @brief Remove a git worktree (does not delete working files)

        @param name Worktree name
        @param force Force removal even with uncommitted changes
        @return Tuple of (success, message)
        """
        logger.info(f"[WorktreeManager] Request to remove worktree '{name}' (force={force})")

        if not self._check_git():
            logger.warning(f"[WorktreeManager] Cannot remove worktree '{name}': not inside a git repository")
            return False, "Not inside a git repository"

        index = self._ensure_index()

        if name not in index.get("worktrees", {}):
            logger.warning(f"[WorktreeManager] Cannot remove worktree '{name}': does not exist")
            return False, f"Worktree '{name}' does not exist"

        worktree_info = index["worktrees"][name]
        worktree_path = Path(worktree_info["path"])

        if worktree_info.get("removed_at"):
            logger.warning(f"[WorktreeManager] Cannot remove worktree '{name}': already removed")
            return False, f"Worktree '{name}' has already been removed"

        logger.info(f"[WorktreeManager] Removing worktree '{name}' from {worktree_path} (force={force})")

        code, stdout, stderr = self._run_git([
            "worktree", "remove",
            worktree_path.as_posix(),
            "--force" if force else "",
        ])

        if code != 0:
            logger.warning(f"[WorktreeManager] Failed to remove worktree '{name}': {stderr or stdout}")
            return False, f"Failed to remove worktree: {stderr or stdout}"

        worktree_info["removed_at"] = time.time()
        self._save_index(index)

        self._event_bus.emit(
            event_type="worktree_removed",
            task_id=worktree_info.get("task_id"),
            worktree_name=name,
        )

        logger.info(f"[WorktreeManager] SUCCESS: Removed worktree '{name}' (files preserved at {worktree_path})")
        return True, f"Worktree '{name}' removed (files preserved at {worktree_path})"

    def list_all(self) -> list[dict]:
        """
        @brief List all worktrees

        @return List of worktree information dictionaries
        """
        index = self._ensure_index()
        worktrees = []

        for name, info in index.get("worktrees", {}).items():
            worktree_entry = {
                "name": name,
                "path": info["path"],
                "base_ref": info.get("base_ref", "HEAD"),
                "task_id": info.get("task_id"),
                "created_at": info.get("created_at"),
                "removed_at": info.get("removed_at"),
                "status": "removed" if info.get("removed_at") else "active",
            }
            worktrees.append(worktree_entry)

        return sorted(worktrees, key=lambda x: x.get("created_at", 0), reverse=True)

    def get(self, name: str) -> Optional[dict]:
        """
        @brief Get information about a specific worktree

        @param name Worktree name
        @return Worktree information or None if not found
        """
        index = self._ensure_index()

        if name not in index.get("worktrees", {}):
            return None

        info = index["worktrees"][name]
        return {
            "name": name,
            "path": info["path"],
            "base_ref": info.get("base_ref", "HEAD"),
            "task_id": info.get("task_id"),
            "created_at": info.get("created_at"),
            "removed_at": info.get("removed_at"),
            "status": "removed" if info.get("removed_at") else "active",
        }
