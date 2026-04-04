"""Background task manager for long-running commands."""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("Nexus")


@dataclass
class BackgroundTask:
    """后台任务"""
    task_id: str
    command: str
    cwd: Optional[str] = None
    status: str = "pending"  # pending, running, completed, timeout, error
    result: Optional[str] = None
    error: Optional[str] = None


class BackgroundTaskManager:
    """后台任务管理器

    使用 asyncio 管理后台任务的执行和状态追踪。
    任务在后台异步执行，完成后通过 drain_notifications() 通知。
    """

    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._notification_queue: list[dict] = []
        self._lock = asyncio.Lock()

    async def run(self, command: str, cwd: str = None) -> str:
        """启动后台任务，立即返回 task_id。

        Args:
            command: 要执行的命令
            cwd: 工作目录

        Returns:
            task_id 字符串
        """
        task_id = str(uuid.uuid4())[:8]

        task = BackgroundTask(
            task_id=task_id,
            command=command,
            cwd=cwd,
            status="running"
        )
        self._tasks[task_id] = task

        asyncio.create_task(self._execute_in_background(task_id, command, cwd))

        logger.info(f"[BackgroundTaskManager] 启动后台任务 {task_id}: {command[:80]}")
        return f"Background task {task_id} started: {command[:80]}"

    async def _execute_in_background(self, task_id: str, command: str, cwd: str = None):
        """在后台异步执行任务。

        Args:
            task_id: 任务 ID
            command: 要执行的命令
            cwd: 工作目录
        """
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            output = (stdout.decode() + stderr.decode()).strip()
            if len(output) > 50000:
                output = output[:50000] + "\n[output truncated]"

            status = "completed" if process.returncode == 0 else "error"
            if not output:
                output = "(no output)"

        except asyncio.TimeoutError:
            output = "Error: Timeout"
            status = "timeout"

        except Exception as e:
            output = f"Error: {e}"
            status = "error"

        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = status
                self._tasks[task_id].result = output

            self._notification_queue.append({
                "task_id": task_id,
                "status": status,
                "command": command[:80],
                "result": output[:500],
            })

        logger.info(f"[BackgroundTaskManager] 任务 {task_id} 完成，状态: {status}")

    async def check(self, task_id: str = None) -> str:
        """查询任务状态。

        Args:
            task_id: 任务 ID，不指定则列出所有任务

        Returns:
            状态信息字符串
        """
        if task_id:
            task = self._tasks.get(task_id)
            if not task:
                return f"Error: Unknown task {task_id}"

            result_preview = task.result[:200] if task.result else "(running)"
            return f"[{task.status}] {task.command[:60]}\n{result_preview}"

        if not self._tasks:
            return "No background tasks."

        lines = []
        for tid, task in self._tasks.items():
            lines.append(f"{tid}: [{task.status}] {task.command[:60]}")
        return "\n".join(lines)

    def drain_notifications(self) -> list[dict]:
        """获取所有已完成任务的通知并清空队列。

        Returns:
            已完成任务列表，每个任务包含 task_id, status, command, result
        """
        notifications = list(self._notification_queue)
        self._notification_queue.clear()
        return notifications

    def peek_notifications(self) -> list[dict]:
        """获取通知但不清空队列。

        Returns:
            已完成任务列表（不删除）
        """
        return list(self._notification_queue)

    def get_running_tasks(self) -> list[str]:
        """获取正在运行的任务 ID 列表。

        Returns:
            正在运行的任务 ID 列表
        """
        return [
            tid for tid, task in self._tasks.items()
            if task.status == "running"
        ]

    def get_all_tasks(self) -> dict[str, BackgroundTask]:
        """获取所有任务。

        Returns:
            任务字典 {task_id: BackgroundTask}
        """
        return self._tasks.copy()


_global_bg_manager: Optional[BackgroundTaskManager] = None


def get_background_manager() -> BackgroundTaskManager:
    """获取全局后台任务管理器实例。"""
    global _global_bg_manager
    if _global_bg_manager is None:
        _global_bg_manager = BackgroundTaskManager()
    return _global_bg_manager
