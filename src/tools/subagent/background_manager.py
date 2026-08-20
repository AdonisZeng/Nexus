"""Background subagent execution manager"""
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional, Awaitable, Callable

from src.utils import get_logger

from .models import SubagentResult

logger = get_logger("subagent.bg_manager")


@dataclass
class BackgroundSubagentTask:
    """后台子代理任务"""
    task_id: str
    subagent_name: str
    prompt: str
    status: str = "pending"  # pending, running, completed, error, cancelled
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)  # 取消事件
    result: Optional[SubagentResult] = None
    error: Optional[str] = None


class BackgroundSubagentManager:
    """
    后台子代理执行管理器。

    类似 BackgroundTaskManager 模式，但用于子代理任务。
    使用 asyncio.create_task() 实现非阻塞执行。
    """

    def __init__(self):
        self._tasks: dict[str, BackgroundSubagentTask] = {}
        self._notification_queue: list[dict] = []
        self._lock = asyncio.Lock()

    async def run(
        self,
        subagent_name: str,
        prompt: str,
        runner_factory: Callable[[], Awaitable[any]],
    ) -> str:
        """
        启动后台子代理任务。

        Args:
            subagent_name: 子代理名称
            prompt: 发送给子代理的提示
            runner_factory: 返回 SubagentRunner 实例的异步工厂函数

        Returns:
            task_id 字符串
        """
        task_id = str(uuid.uuid4())[:8]

        task = BackgroundSubagentTask(
            task_id=task_id,
            subagent_name=subagent_name,
            prompt=prompt,
            status="running",
            cancel_event=asyncio.Event(),
        )
        self._tasks[task_id] = task

        # 在后台启动执行
        asyncio.create_task(self._execute_in_background(task_id, runner_factory))

        logger.info(
            f"[BackgroundSubagentManager] Started background subagent "
            f"{subagent_name} with task_id={task_id}"
        )
        return task_id

    async def _execute_in_background(
        self,
        task_id: str,
        runner_factory: Callable[[], Awaitable[any]],
    ) -> None:
        """在后台执行子代理"""
        task = self._tasks.get(task_id)
        if not task:
            return

        try:
            # 检查取消事件
            if task.cancel_event.is_set():
                async with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].status = "cancelled"
                logger.info(f"[BackgroundSubagentManager] Task {task_id} was cancelled before start")
                return

            runner = await runner_factory()
            result = await runner.run(task.prompt)

            async with self._lock:
                if task_id in self._tasks:
                    if task.cancel_event.is_set():
                        self._tasks[task_id].status = "cancelled"
                        logger.info(f"[BackgroundSubagentManager] Task {task_id} cancelled after completion")
                        return
                    self._tasks[task_id].status = "completed"
                    self._tasks[task_id].result = result

                self._notification_queue.append({
                    "task_id": task_id,
                    "status": "completed",
                    "subagent_name": task.subagent_name,
                    "result": result.output[:500] if result and result.output else "",
                })

            logger.info(f"[BackgroundSubagentManager] Task {task_id} completed")

        except asyncio.CancelledError:
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = "cancelled"
                    self._tasks[task_id].error = "Task cancelled"

                self._notification_queue.append({
                    "task_id": task_id,
                    "status": "cancelled",
                    "subagent_name": task.subagent_name,
                    "error": "Task cancelled",
                })
            logger.info(f"[BackgroundSubagentManager] Task {task_id} cancelled")

        except Exception as e:
            async with self._lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = "error"
                    self._tasks[task_id].error = str(e)

                self._notification_queue.append({
                    "task_id": task_id,
                    "status": "error",
                    "subagent_name": task.subagent_name,
                    "error": str(e),
                })

            logger.error(f"[BackgroundSubagentManager] Task {task_id} failed: {e}")

    async def check(self, task_id: Optional[str] = None) -> str:
        """
        检查后台子代理任务状态。

        Args:
            task_id: 要检查的任务 ID，None 则列出所有任务

        Returns:
            状态描述字符串
        """
        if task_id:
            task = self._tasks.get(task_id)
            if not task:
                return f"Error: Unknown task {task_id}"

            if task.status == "completed" and task.result:
                preview = task.result.output[:200] if task.result.output else "[no output]"
                return f"[{task.status}] {task.subagent_name}\n{preview}"
            elif task.status == "error":
                return f"[{task.status}] {task.subagent_name}\nError: {task.error}"
            else:
                return f"[{task.status}] {task.subagent_name}\nRunning..."

        if not self._tasks:
            return "No background subagent tasks."

        lines = []
        for tid, task in self._tasks.items():
            lines.append(f"{tid}: [{task.status}] {task.subagent_name}")
        return "\n".join(lines)

    def drain_notifications(self) -> list[dict]:
        """
        获取所有已完成任务的通知并清空队列。

        Returns:
            通知字典列表
        """
        notifications = list(self._notification_queue)
        self._notification_queue.clear()
        return notifications

    def peek_notifications(self) -> list[dict]:
        """
        获取所有已完成任务的通知（不清空队列）。

        Returns:
            通知字典列表
        """
        return list(self._notification_queue)

    def get_running_tasks(self) -> list[str]:
        """
        获取正在运行的任务 ID 列表。

        Returns:
            任务 ID 列表
        """
        return [
            tid for tid, task in self._tasks.items()
            if task.status == "running"
        ]

    def get_all_tasks(self) -> dict[str, BackgroundSubagentTask]:
        """获取所有任务"""
        return self._tasks.copy()

    def get_task(self, task_id: str) -> Optional[BackgroundSubagentTask]:
        """获取指定任务"""
        return self._tasks.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        """
        取消一个后台子代理任务。

        Args:
            task_id: 要取消的任务 ID

        Returns:
            True 如果成功发起取消，False 如果任务不存在或已完成
        """
        async with self._lock:
            if task_id not in self._tasks:
                return False

            task = self._tasks[task_id]

            if task.status != "running":
                return False

            task.cancel_event.set()
            task.status = "cancelled"

            logger.info(f"[BackgroundSubagentManager] Cancellation requested for task {task_id}")
            return True


# 全局实例
_global_bg_subagent_manager: Optional[BackgroundSubagentManager] = None


def get_bg_subagent_manager() -> BackgroundSubagentManager:
    """获取全局 BackgroundSubagentManager 实例"""
    global _global_bg_subagent_manager
    if _global_bg_subagent_manager is None:
        _global_bg_subagent_manager = BackgroundSubagentManager()
    return _global_bg_subagent_manager


__all__ = [
    "BackgroundSubagentTask",
    "BackgroundSubagentManager",
    "get_bg_subagent_manager",
]
