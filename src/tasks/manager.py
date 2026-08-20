"""TaskManager - 支持依赖图的持久化任务管理器

提供任务的 CRUD 操作和依赖图管理功能。
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from .models import Task

logger = logging.getLogger("Nexus")

TASKS_ROOT = Path.cwd() / ".nexus" / "tasks"


def sanitize_folder_name(name: str) -> str:
    """将项目名称转换为安全的文件夹名称

    @param name: 原始项目名称
    @return: 安全的文件夹名称
    """
    name = name.strip()
    name = re.sub(r'[/\\:*?"<>|]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = name[:50]
    name = name.strip('_')
    return name or "unnamed_project"


class TaskManager:
    """任务管理器

    支持依赖图的持久化任务管理器。每个任务存储为单独的 JSON 文件。

    Attributes:
        dir: 任务存储目录路径
        project_name: 项目名称
    """

    def __init__(self, tasks_dir: Optional[Path] = None, project_name: Optional[str] = None):
        """初始化任务管理器

        @param tasks_dir: 任务存储目录（完整路径）
        @param project_name: 项目名称（用于创建子目录）
        """
        if tasks_dir is None:
            if project_name:
                safe_name = sanitize_folder_name(project_name)
                tasks_dir = TASKS_ROOT / safe_name
            else:
                tasks_dir = TASKS_ROOT
        self.dir = tasks_dir
        self.project_name = project_name or "default"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._next_id = self._max_id() + 1
        logger.info(f"[TaskManager] 初始化完成，项目: {self.project_name}，存储目录: {self.dir}")

    def _max_id(self) -> int:
        """获取当前最大任务 ID

        @return: 最大任务 ID，如果无任务则返回 0
        """
        if not self.dir.exists():
            return 0
        ids = []
        for f in self.dir.glob("task_*.json"):
            try:
                parts = f.stem.split("_")
                if len(parts) == 2:
                    ids.append(int(parts[1]))
            except ValueError:
                continue
        return max(ids) if ids else 0

    def _load(self, task_id: int) -> Task:
        """加载指定任务

        @param task_id: 任务 ID
        @return: Task 实例
        @raises ValueError: 任务不存在时
        """
        path = self.dir / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Task.from_dict(data)

    def _save(self, task: Task) -> None:
        """保存任务到文件

        @param task: Task 实例
        """
        path = self.dir / f"task_{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.debug(f"[TaskManager] 保存任务 {task.id}: {task.subject}")

    def create(self, subject: str, description: str = "") -> str:
        """创建新任务

        @param subject: 任务主题
        @param description: 任务描述（可选）
        @return: 创建的任务详情（JSON 字符串）
        """
        task = Task(
            id=self._next_id,
            subject=subject,
            description=description,
            status="pending",
            blocked_by=[],
            blocks=[],
            owner="",
        )
        self._save(task)
        self._next_id += 1
        logger.info(f"[TaskManager] 创建任务 #{task.id}: {task.subject}")
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def get(self, task_id: int) -> str:
        """获取单个任务详情

        @param task_id: 任务 ID
        @return: 任务详情（JSON 字符串）
        """
        task = self._load(task_id)
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def update(
        self,
        task_id: int,
        status: Optional[str] = None,
        add_blocked_by: Optional[list[int]] = None,
        add_blocks: Optional[list[int]] = None,
    ) -> str:
        """更新任务状态或依赖

        @param task_id: 任务 ID
        @param status: 新状态（pending/in_progress/completed）
        @param add_blocked_by: 添加阻塞此任务的任务 ID 列表
        @param add_blocks: 添加被此任务阻塞的任务 ID 列表
        @return: 更新后的任务详情（JSON 字符串）
        """
        task = self._load(task_id)

        if status:
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Invalid status: {status}")
            task.status = status
            if status == "completed":
                self._clear_dependency(task_id)

        if add_blocked_by:
            task.blocked_by = list(set(task.blocked_by + add_blocked_by))

        if add_blocks:
            task.blocks = list(set(task.blocks + add_blocks))
            for blocked_id in add_blocks:
                try:
                    blocked_task = self._load(blocked_id)
                    if task_id not in blocked_task.blocked_by:
                        blocked_task.blocked_by.append(task_id)
                        self._save(blocked_task)
                except ValueError:
                    logger.warning(f"[TaskManager] 任务 {blocked_id} 不存在，跳过依赖更新")

        self._save(task)
        logger.info(f"[TaskManager] 更新任务 #{task_id}: status={task.status}")
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)

    def _clear_dependency(self, completed_id: int) -> None:
        """清除已完成任务的依赖

        当任务完成时，从所有其他任务的 blocked_by 列表中移除该任务。

        @param completed_id: 已完成的任务 ID
        """
        if not self.dir.exists():
            logger.warning(f"[TaskManager] _clear_dependency: 目录不存在 {self.dir}")
            return
        cleared_count = 0
        for f in self.dir.glob("task_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                blocked_by = data.get("blocked_by", [])
                if completed_id in blocked_by:
                    blocked_by.remove(completed_id)
                    data["blocked_by"] = blocked_by
                    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    cleared_count += 1
                    logger.info(f"[TaskManager] _clear_dependency({completed_id}): 清除任务 {data['id']} 的依赖，剩余 blocked_by={blocked_by}")
            except (json.JSONDecodeError, ValueError):
                continue
        logger.info(f"[TaskManager] _clear_dependency({completed_id}): 共清除了 {cleared_count} 个任务的依赖")

    def list_all(self) -> str:
        """列出所有任务

        @return: 任务列表（带状态标记和依赖信息）
        """
        if not self.dir.exists():
            return "No tasks."
        tasks = []
        for f in sorted(self.dir.glob("task_*.json")):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue

        if not tasks:
            return "No tasks."

        lines = []
        for t in sorted(tasks, key=lambda x: x["id"]):
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }.get(t["status"], "[?]")
            blocked = f" (blocked by: {t['blocked_by']})" if t.get("blocked_by") else ""
            lines.append(f"{marker} #{t['id']}: {t['subject']}{blocked}")

        completed = sum(1 for t in tasks if t["status"] == "completed")
        lines.append(f"\n({completed}/{len(tasks)} completed)")
        return "\n".join(lines)

    def get_ready_tasks(self) -> list[Task]:
        """获取所有就绪的任务（未被阻塞且未完成）

        @return: 就绪任务列表
        """
        ready = []
        for f in self.dir.glob("task_*.json"):
            try:
                task = Task.from_dict(json.loads(f.read_text(encoding="utf-8")))
                if task.is_ready():
                    ready.append(task)
            except (json.JSONDecodeError, ValueError):
                continue
        if ready:
            logger.info(f"[TaskManager] get_ready_tasks: 返回 {len(ready)} 个就绪任务: {[t.id for t in ready]}")
        return ready

    def get_all_tasks(self) -> list[Task]:
        """获取所有任务

        @return: 任务列表
        """
        tasks = []
        for f in sorted(self.dir.glob("task_*.json")):
            try:
                tasks.append(Task.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, ValueError):
                continue
        return tasks

    def get_progress(self) -> tuple[int, int]:
        """获取执行进度

        @return: (已完成数, 总数)
        """
        tasks = self.get_all_tasks()
        completed = sum(1 for t in tasks if t.status == "completed")
        return (completed, len(tasks))

    def is_all_completed(self) -> bool:
        """检查是否所有任务都已完成

        @return: 如果全部完成则返回 True
        """
        tasks = self.get_all_tasks()
        if not tasks:
            return False
        return all(t.status == "completed" for t in tasks)


_global_managers: dict[str, TaskManager] = {}


def get_task_manager(project_name: Optional[str] = None) -> TaskManager:
    """获取全局 TaskManager 实例

    @param project_name: 项目名称（可选），用于创建子目录
    @return: 全局 TaskManager 实例
    """
    global _global_managers
    if project_name:
        safe_name = sanitize_folder_name(project_name)
        if safe_name not in _global_managers:
            _global_managers[safe_name] = TaskManager(project_name=project_name)
        return _global_managers[safe_name]
    if _global_managers.get("default") is None:
        _global_managers["default"] = TaskManager()
    return _global_managers["default"]
