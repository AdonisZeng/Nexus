"""Tasks Mode Manager - Tasks 交互模式管理器

管理 Tasks 模式的交互流程，包括任务分析、依赖配置和自动执行。
"""

import asyncio
import json
import logging
import re
from typing import Optional, Tuple

from ..agent import AgentEvent, EventType, WorkItemSource, WorkItem
from ..cli.rich_ui import console, input_with_prompt, print_tasks_confirmation
from .manager import TaskManager, get_task_manager

logger = logging.getLogger("Nexus")


class TasksModeWorkItemSource(WorkItemSource):
    """DAG感知的 WorkItemSource for Tasks 模式"""

    def __init__(self, tasks_manager: "TasksModeManager"):
        self.tasks_manager = tasks_manager
        self._executed_task_ids: set[str] = set()  # 统一使用 str

    async def get_next_work_item(self) -> WorkItem | None:
        """获取下一个就绪的任务"""
        ready_tasks = self.tasks_manager.task_manager.get_ready_tasks()
        logger.info(f"[TasksModeWorkItemSource] get_next_work_item: 就绪任务数={len(ready_tasks)}, 已执行={self._executed_task_ids}")
        # 过滤掉已执行的任务
        for task in ready_tasks:
            task_id_str = str(task.id)
            if task_id_str not in self._executed_task_ids:
                logger.info(f"[TasksModeWorkItemSource] get_next_work_item: 返回任务 #{task.id}: {task.subject}")
                return WorkItem(
                    id=task_id_str,
                    description=task.subject,
                    context={"task": task}
                )
        logger.info(f"[TasksModeWorkItemSource] get_next_work_item: 没有更多就绪任务")
        return None

    async def on_work_item_completed(self, item: WorkItem, result: str) -> None:
        """标记任务完成，解锁依赖它的任务"""
        logger.info(f"[TasksModeWorkItemSource] on_work_item_completed: item.id={item.id}, result={result[:50] if result else None}")
        task_id_str = item.id
        self._executed_task_ids.add(task_id_str)

        # 根据结果更新状态
        if result and not result.startswith("[Error]"):
            logger.info(f"[TasksModeWorkItemSource] on_work_item_completed: 标记任务 #{task_id_str} 为 completed")
            self.tasks_manager.task_manager.update(int(task_id_str), status="completed")
        else:
            logger.info(f"[TasksModeWorkItemSource] on_work_item_completed: 标记任务 #{task_id_str} 为 pending")
            self.tasks_manager.task_manager.update(int(task_id_str), status="pending")

    def has_more_work(self) -> bool:
        """检查是否还有就绪的任务"""
        ready_tasks = self.tasks_manager.task_manager.get_ready_tasks()
        return any(str(task.id) not in self._executed_task_ids for task in ready_tasks)


class TasksModeManager:
    """Tasks 模式管理器

    管理 Tasks 交互模式的工作流程：
    1. 用户输入任务描述
    2. LLM 分析并生成任务图（包含项目名称和依赖关系）
    3. 自动创建任务并配置依赖
    4. 按依赖顺序自动执行任务
    """

    def __init__(self, session):
        """初始化 Tasks 模式管理器

        @param session: AgentSession 实例（提供 model_adapter, system_prompt, execute_task）
        """
        self.cli = session  # kept as self.cli for minimal diff; now an AgentSession
        self.active = False
        self.task_manager: Optional[TaskManager] = None
        self.project_name: Optional[str] = None

    def enter(self) -> None:
        """进入 Tasks 模式"""
        self.active = True

    def exit(self) -> None:
        """退出 Tasks 模式"""
        self.active = False
        self.task_manager = None
        self.project_name = None

    def is_active(self) -> bool:
        """检查是否在 Tasks 模式中

        @return: 是否活跃
        """
        return self.active

    async def analyze_and_create_tasks(self, task_description: str) -> bool:
        """分析任务描述并创建任务图

        调用 LLM 分析复杂任务，生成项目名称、任务列表和依赖关系，
        然后自动创建任务并配置依赖。

        @param task_description: 用户输入的任务描述
        @return: 是否成功
        """
        from ..cli.rich_ui import print_tasks_status

        print_tasks_status("正在分析任务...", "analyzing")

        prompt = f"""你是一个任务规划助手，擅长将复杂任务分解为有序的执行步骤。

{task_description}

请完成以下两个任务：

**任务1：生成项目名称**
根据任务描述提取或生成一个简短的项目名称（name），用于创建文件夹。
- 名称应该简洁，控制在20个字符以内
- 例如："俄罗斯方块"、"用户认证模块"、"数据看板"

**任务2：分解任务并建立依赖图**
将任务分解为具体的执行步骤，并为每个步骤建立依赖关系。

核心原则：
- 每个任务对应一个**具体的阶段性成果**（如需求分析、规划、代码文件、测试等）
- 可以包括：需求分析、项目规划、文件生成、测试验证等不同类型
- 一个代码文件任务应该包含该模块的**完整功能代码**，而不是分多次添加
- 分析规划类任务通常是第一个任务，为后续文件生成提供指导

依赖关系原则：
- 只有当被依赖的任务完成后，后续任务才能开始
- 分析规划 → 文件生成 → 测试验证（按此顺序）
- 并行无依赖的任务可以同时执行

请用以下 JSON 格式返回：
{{
    "name": "项目名称（20字以内）",
    "tasks": [
        {{
            "id": 1,
            "subject": "分析需求并规划项目结构",
            "description": "分析功能需求，确定技术方案和项目结构",
            "blocked_by": []
        }},
        {{
            "id": 2,
            "subject": "生成 SPEC.md",
            "description": "项目规范文档，包含功能需求、技术方案",
            "blocked_by": [1]
        }},
        {{
            "id": 3,
            "subject": "生成 index.html",
            "description": "完整的 HTML 结构，包含所有 UI 组件",
            "blocked_by": [1]
        }},
        {{
            "id": 4,
            "subject": "生成 game.js",
            "description": "完整的游戏逻辑，包括棋盘、棋子、规则、状态管理",
            "blocked_by": [2, 3]
        }},
        {{
            "id": 5,
            "subject": "生成 styles.css",
            "description": "完整的样式文件",
            "blocked_by": [1]
        }}
    ]
}}

关键要求：
1. **blocked_by 字段必须填写** - 每个任务都必须有 blocked_by 数组，即使第一个任务也要填 `[]`
2. **第一个任务的 blocked_by 必须是空数组 `[]`**
3. **后续任务的 blocked_by 应该包含它所依赖的任务 ID**
4. **如果任务A依赖任务B的结果，则A的 blocked_by 应包含 B 的 id**
5. **可以并行的任务可以指向同一个前置任务**

错误示例（太细碎）：
```json
{{"id": 2, "subject": "实现棋盘界面", "description": "xxx", "blocked_by": [1]}}
{{"id": 3, "subject": "实现棋子渲染", "description": "xxx", "blocked_by": [1]}}
```
正确示例（一个文件一个完整任务）：
```json
{{"id": 2, "subject": "生成 index.html", "description": "完整的 HTML 结构和棋盘 UI", "blocked_by": [1]}}
{{"id": 3, "subject": "生成 game.js", "description": "完整的游戏逻辑包括棋盘、棋子、规则", "blocked_by": [1]}}
```

请只返回 JSON，不要有任何其他文字说明："""

        messages = [{"role": "user", "content": prompt}]

        # 检查消息长度，超过阈值时警告
        total_chars = sum(len(m["content"]) for m in messages)
        if total_chars > 50000:
            logger.warning(f"[TasksMode] analyze_and_create_tasks 消息过长 ({total_chars} chars)，可能导致分析失败")

        try:
            response = await self.cli.model_adapter.chat(
                messages, self.cli.system_prompt
            )
        except Exception as e:
            logger.error(f"[TasksMode] LLM 调用失败: {e}")
            print_tasks_status(f"分析失败: {e}", "error")
            return False

        project_name, tasks_data = self._parse_response(response)
        if not tasks_data:
            logger.warning("[TasksMode] 无法解析任务数据，尝试文本解析")
            project_name, tasks_data = self._parse_response_text(response, task_description)

        if not tasks_data:
            print_tasks_status("无法生成任务计划，请尝试更详细地描述任务", "error")
            return False

        self.project_name = project_name
        print_tasks_status(f"项目: {project_name}", "info")
        self._create_tasks_from_data(tasks_data)

        from ..cli.rich_ui import print_task_dependency_graph
        print_task_dependency_graph(self.task_manager.get_all_tasks())

        return True

    def _parse_response(self, response: str) -> Tuple[Optional[str], Optional[list]]:
        """解析 LLM 响应

        @param response: LLM 响应
        @return: (项目名称, 任务数据列表)
        """
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                name = data.get("name", "unnamed_project")
                tasks = data.get("tasks", [])
                if tasks:
                    logger.info(f"[TasksMode] 解析成功，项目: {name}，任务数: {len(tasks)}")
                    return (name, tasks)
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[TasksMode] JSON 解析失败: {e}")
        return (None, None)

    def _parse_response_text(self, response: str, task_description: str) -> Tuple[str, Optional[list]]:
        """从文本中解析项目名称和任务列表（备用方案）

        @param response: LLM 响应
        @param task_description: 原始任务描述
        @return: (项目名称, 任务数据列表)
        """
        logger.info(f"[TasksMode] 使用文本解析，response长度={len(response)}")

        project_name = self._extract_project_name(task_description)

        lines = response.strip().split("\n")
        tasks = []
        task_pattern = re.compile(r'^[\d\.\)\-\*]+[\s]*(.+)')

        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            if line.startswith('<') or 'thinking' in line.lower():
                continue

            match = task_pattern.match(line)
            if match:
                task_text = match.group(1).strip()
                if task_text and len(task_text) > 5:
                    tasks.append({
                        "id": len(tasks) + 1,
                        "subject": task_text,
                        "description": "",
                        "blocked_by": [len(tasks)] if tasks else []
                    })

        if tasks:
            for i, task in enumerate(tasks):
                task["id"] = i + 1
                if i == 0:
                    task["blocked_by"] = []
                else:
                    task["blocked_by"] = [i]

        logger.info(f"[TasksMode] 文本解析出 {len(tasks)} 个任务")
        return (project_name, tasks if tasks else None)

    def _extract_project_name(self, task_description: str) -> str:
        """从任务描述中提取项目名称

        @param task_description: 任务描述
        @return: 简短的项目名称
        """
        from .manager import sanitize_folder_name

        task_description = task_description.strip()
        words = task_description.split()
        if len(words) <= 5:
            name = task_description
        else:
            name = " ".join(words[:5])

        name = re.sub(r'[帮请帮我帮我创建开发设计实现一个]', '', name)
        name = re.sub(r'一个|项目|系统|应用|程序', '', name)
        name = name.strip()
        name = name[:20]

        if not name:
            name = "unnamed_project"

        return sanitize_folder_name(name)

    def _create_tasks_from_data(self, tasks_data: list) -> None:
        """从数据创建任务并配置依赖

        @param tasks_data: 任务数据列表
        """
        self.task_manager = get_task_manager(project_name=self.project_name)

        created_tasks = {}

        for task_data in tasks_data:
            subject = task_data.get("subject", "")
            description = task_data.get("description", "")
            task_json = self.task_manager.create(subject, description)
            task_obj = json.loads(task_json)
            created_tasks[task_data["id"]] = task_obj["id"]
            logger.info(f"[TasksMode] 创建任务 #{task_obj['id']}: {subject}")

        for task_data in tasks_data:
            task_id = task_data.get("id")
            blocked_by = task_data.get("blocked_by", [])

            if blocked_by:
                actual_task_id = created_tasks.get(task_id)
                if actual_task_id:
                    actual_blocked_by = [created_tasks.get(bid) for bid in blocked_by if bid in created_tasks]
                    actual_blocked_by = [bid for bid in actual_blocked_by if bid is not None]
                    if actual_blocked_by:
                        self.task_manager.update(actual_task_id, add_blocked_by=actual_blocked_by)
                        logger.info(f"[TasksMode] 任务 #{actual_task_id} 的 blocked_by: {actual_blocked_by}")

    async def execute_ready_tasks(self) -> bool:
        """执行所有就绪的任务

        按依赖顺序执行 blocked_by 为空且状态为 pending 的任务。

        @return: 是否全部成功完成
        """
        from ..cli.rich_ui import (
            print_tasks_status,
            print_task_dependency_graph,
        )
        from src.agent import AgentLoop

        work_item_source = TasksModeWorkItemSource(self)

        # Access the loop instance via closure
        _loop = None

        async def on_iteration_start(iteration: int):
            """每个任务开始前显示依赖图"""
            nonlocal _loop
            if _loop is None:
                return
            item = _loop.current_work_item
            if item is None:
                return

            task_id = int(item.id)
            self.task_manager.update(task_id, status="in_progress")
            console.print()
            console.print(f"[bold yellow]▶ 执行任务 #{task_id}: {item.description}[/bold yellow]")
            console.print()
            print_task_dependency_graph(self.task_manager.get_all_tasks())

        async def on_iteration_end(iteration: int, success: bool, elapsed: float):
            """每个任务完成后显示更新的依赖图"""
            print_task_dependency_graph(self.task_manager.get_all_tasks())

        loop = AgentLoop(
            work_item_source=work_item_source,
            on_iteration_start=on_iteration_start,
            on_iteration_end=on_iteration_end,
            on_confirmation_check=self._tasks_confirmation_check,
        )
        _loop = loop

        async def execute_fn():
            """执行当前的 work item"""
            return await self._execute_work_item(_loop.current_work_item)

        try:
            await loop.run(execute_fn)
        except asyncio.CancelledError:
            print_tasks_status("任务执行被取消", "warning")
            return False

        # 检查是否全部完成
        if self.task_manager.is_all_completed():
            return True

        # 检查是否有任务被阻塞
        pending = [t for t in self.task_manager.get_all_tasks() if t.status == "pending"]
        blocked_tasks = [t for t in pending if t.is_blocked()]
        if blocked_tasks:
            logger.info(f"[TasksMode] 有 {len(blocked_tasks)} 个任务被阻塞")
            return False

        return False

    async def _execute_work_item(self, work_item: WorkItem) -> tuple[str, list[dict], str]:
        """执行单个 work item 并返回 (result, tool_calls, stop_reason)

        Args:
            work_item: 要执行的工作项

        Returns:
            Tuple of (result string, list of tool calls, stop_reason string)
        """
        task = work_item.context["task"]
        context = self._build_task_context(task)
        results = []
        tool_calls_found = []
        tool_results = []  # 收集 tool results
        has_error = False
        stop_reason = None

        try:
            async for event in self.cli.execute_task(context):
                if event.type == EventType.THINKING:
                    pass
                elif event.type == EventType.TOOL_CALL:
                    tool_calls_found.append(event.metadata)
                elif event.type == EventType.TOOL_RESULT:
                    # 收集 tool results 以便后续处理
                    tool_results.append(event.content)
                elif event.type == EventType.OUTPUT:
                    pass
                elif event.type == EventType.ERROR:
                    results.append(f"[Error] {event.content}")
                    has_error = True
                elif event.type == EventType.DONE:
                    # 从 metadata 获取 stop_reason
                    stop_reason = event.metadata.get("stop_reason") if event.metadata else None

            if has_error:
                return (f"[Error] {'; '.join(results)}", tool_calls_found, stop_reason or "error")
            return ("任务完成", tool_calls_found, stop_reason or "stop")
        except Exception as e:
            return (f"[Error] {str(e)}", tool_calls_found, "error")

    async def _tasks_confirmation_check(self, response: str, stop_reason: str) -> Optional[bool]:
        """Tasks 模式的任务完成确认

        Args:
            response: LLM 响应文本
            stop_reason: 停止原因

        Returns:
            True - 确认完成
            False - 未确认完成
            None - 跳过确认
        """
        logger.info(f"[_tasks_confirmation_check] response={response[:50] if response else None}, stop_reason={stop_reason}")
        # Tasks 模式通过 TaskManager 管理任务状态
        # 如果所有任务都完成了，跳过确认
        if self.task_manager.is_all_completed():
            logger.info(f"[_tasks_confirmation_check] 全部任务已完成，返回 True")
            return True
        # 如果确认响应是"任务完成"，即使还有任务也标记当前任务完成
        if response and "任务完成" in response:
            logger.info(f"[_tasks_confirmation_check] 确认响应包含'任务完成'，返回 True")
            return True
        # 如果 stop_reason 是 error，表示执行出错
        if stop_reason == "error":
            logger.info(f"[_tasks_confirmation_check] stop_reason=error，返回 False")
            return False
        # 如果还有任务未完成，需要继续
        logger.info(f"[_tasks_confirmation_check] 任务未完成，返回 False")
        return False

    def _build_task_context(self, current_task) -> str:
        """构建任务执行上下文

        将项目名称、当前任务、任务描述和项目整体任务列表组合成完整上下文。

        @param current_task: 当前要执行的任务
        @return: 完整的任务上下文字符串
        """
        lines = []
        lines.append("=" * 60)
        lines.append("【项目信息】")
        lines.append(f"项目名称: {self.project_name}")
        lines.append("")
        lines.append("【当前任务】")
        lines.append(f"任务 #{current_task.id}: {current_task.subject}")
        if current_task.description:
            lines.append(f"任务描述: {current_task.description}")

        all_tasks = self.task_manager.get_all_tasks()
        if len(all_tasks) > 1:
            lines.append("")
            lines.append("【项目完整任务列表】")
            lines.append("-" * 40)
            for t in sorted(all_tasks, key=lambda x: x.id):
                marker = {
                    "pending": "[ ]",
                    "in_progress": "[>]",
                    "completed": "[x]",
                }.get(t.status, "[?]")
                prefix = "→ " if t.id == current_task.id else "  "
                blocked = f" ← 等待 #{', '.join(map(str, t.blocked_by))}" if t.blocked_by else ""
                lines.append(f"{prefix}{marker} #{t.id}: {t.subject}{blocked}")

        lines.append("=" * 60)
        lines.append("")
        lines.append("【重要约束 - 请严格遵守】")
        lines.append("1. 每个任务生成一个完整的文件，包含该模块的全部功能代码")
        lines.append("2. 如果确实需要对已生成的文件进行修改，在后续任务中说明")
        lines.append("3. 文件间有引用关系时（如 HTML 引用 JS），确保被引用的文件已存在或先生成")
        lines.append("4. 完成后直接返回，不要继续生成其他文件")
        lines.append("")
        lines.append(f"请只完成: {current_task.subject}")

        return "\n".join(lines)

    async def run(self, task_description: str) -> bool:
        """运行完整的 Tasks 工作流程

        @param task_description: 任务描述
        @return: 是否全部成功
        """
        from ..cli.rich_ui import print_tasks_status

        success = await self.analyze_and_create_tasks(task_description)
        if not success:
            return False

        # 等待用户确认后再执行
        all_tasks = self.task_manager.get_all_tasks()
        tasks_for_confirmation = [
            {"id": t.id, "subject": t.subject, "blocked_by": t.blocked_by}
            for t in all_tasks
        ]

        print_tasks_confirmation(tasks_for_confirmation)
        console.print()
        choice = input_with_prompt("确认执行 (y/n): ").strip().lower()

        if choice not in ('y', 'yes', '是'):
            print_tasks_status("用户取消执行", "info")
            return False

        success = await self.execute_ready_tasks()

        completed, total = self.task_manager.get_progress()
        if success and self.task_manager.is_all_completed():
            print_tasks_status(f"🎉 所有 {total} 个任务已完成！", "completed")
        elif completed < total:
            print_tasks_status(f"完成 {completed}/{total} 个任务", "info")

        return success


__all__ = ["TasksModeManager", "TasksModeWorkItemSource"]
