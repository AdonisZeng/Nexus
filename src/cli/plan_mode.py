"""Plan Mode Manager - 计划模式管理器"""
import asyncio
import re
import logging
from typing import Callable, Awaitable, Optional
from dataclasses import dataclass, field

from src.agent import AgentEvent, EventType, WorkItemSource, WorkItem
from src.todo.manager import TodoManager

logger = logging.getLogger("Nexus")

# Import UI functions
from src.cli.rich_ui import (
    print_plan_confirmation,
    print_plan_detail,
    print_plan_status,
    input_with_prompt,
    console,
)


@dataclass
class TaskItem:
    """任务项"""
    description: str
    completed: bool = False
    result: str = ""


class PlanModeWorkItemSource(WorkItemSource):
    """Work item source for Plan mode with sequential task execution."""

    def __init__(self, plan_manager: "PlanModeManager"):
        self.plan_manager = plan_manager
        self._current_index = 0

    async def get_next_work_item(self) -> Optional[WorkItem]:
        """Get the next task as a work item."""
        logger.info(f"[PlanModeWorkItemSource.get_next_work_item] _current_index={self._current_index}, total_tasks={len(self.plan_manager.tasks)}")
        if self._current_index >= len(self.plan_manager.tasks):
            logger.info(f"[PlanModeWorkItemSource.get_next_work_item] No more work items, index {self._current_index} >= {len(self.plan_manager.tasks)}")
            return None
        task = self.plan_manager.tasks[self._current_index]
        logger.info(f"[PlanModeWorkItemSource.get_next_work_item] Returning task {self._current_index}: {task.description[:50]}...")
        return WorkItem(
            id=str(self._current_index),
            description=task.description,
            context={"task_index": self._current_index}
        )

    async def on_work_item_completed(self, item: WorkItem, result: str) -> None:
        """Mark the current task as completed."""
        index = int(item.id)
        logger.info(f"[PlanModeWorkItemSource.on_work_item_completed] ===== 任务完成回调 =====")
        logger.info(f"[PlanModeWorkItemSource.on_work_item_completed] item.id={item.id}, index={index}")
        logger.info(f"[PlanModeWorkItemSource.on_work_item_completed] _current_index before={self._current_index}")
        logger.info(f"[PlanModeWorkItemSource.on_work_item_completed] result: {result[:200] if result else 'empty'}")
        self.plan_manager.complete_task(index, result)
        self._current_index += 1
        logger.info(f"[PlanModeWorkItemSource.on_work_item_completed] _current_index after={self._current_index}")
        logger.info(f"[PlanModeWorkItemSource.on_work_item_completed] ===== 任务完成回调结束 =====")

    def has_more_work(self) -> bool:
        """Check if there are more tasks to process."""
        return self._current_index < len(self.plan_manager.tasks)


class PlanModeManager:
    """计划模式管理器"""

    def __init__(self, cli_instance):
        """
        @brief 初始化计划模式管理器
        @param cli_instance NexusCLI 实例
        """
        self.cli = cli_instance
        self.active = False
        self.tasks: list[TaskItem] = []
        self.current_task_index: int = -1
        self.original_planning_state = False
        self.todo_manager = TodoManager()
        self._reanalysis_count = 0

    def enter(self) -> None:
        """进入计划模式"""
        self.active = True
        self.tasks = []
        self.current_task_index = -1
        self.todo_manager = TodoManager()
        self._reanalysis_count = 0

    def exit(self) -> None:
        """退出计划模式"""
        self.active = False
        self.tasks = []
        self.current_task_index = -1
        self.todo_manager = TodoManager()

    def get_todo_render(self) -> str:
        """
        @brief 获取当前 todo 状态的渲染字符串
        @return todo_manager.render() 的结果
        """
        return self.todo_manager.render()

    def set_tasks(self, tasks: list[str]) -> None:
        """
        @brief 设置任务列表
        @param tasks 任务描述列表
        """
        self.tasks = [TaskItem(description=t) for t in tasks]
        self.current_task_index = -1
        todo_items = [
            {"id": str(i + 1), "text": task, "status": "pending"}
            for i, task in enumerate(tasks)
        ]
        self.todo_manager.update(todo_items)

    def get_pending_tasks(self) -> list[str]:
        """获取待执行的任务描述列表"""
        return [t.description for t in self.tasks if not t.completed]

    def get_all_tasks(self) -> list[str]:
        """获取所有任务描述列表"""
        return [t.description for t in self.tasks]

    def get_completed_indices(self) -> set[int]:
        """获取已完成的任务索引集合"""
        return {i for i, t in enumerate(self.tasks) if t.completed}

    def complete_task(self, index: int, result: str = "") -> None:
        """
        @brief 标记任务为完成
        @param index 任务索引
        @param result 任务执行结果
        """
        if 0 <= index < len(self.tasks):
            self.tasks[index].completed = True
            self.tasks[index].result = result
            self.current_task_index = -1
        for item in self.todo_manager.items:
            if item.id == str(index + 1):
                item.status = "completed"
                break

    def get_current_task_index(self) -> int:
        """获取当前执行的任务索引"""
        return self.current_task_index

    def set_current_task(self, index: int) -> None:
        """
        @brief 设置当前正在执行的任务
        @param index 任务索引
        """
        self.current_task_index = index
        for item in self.todo_manager.items:
            if item.id == str(index + 1):
                item.status = "in_progress"
                break

    def is_all_completed(self) -> bool:
        """检查是否所有任务都已完成"""
        return all(t.completed for t in self.tasks)

    def get_progress(self) -> tuple[int, int]:
        """
        @brief 获取进度
        @return (已完成数, 总数)
        """
        completed = sum(1 for t in self.tasks if t.completed)
        return (completed, len(self.tasks))

    async def analyze_task(self, task_description: str) -> list[str]:
        """
        @brief 使用 AI 分析任务并生成执行计划
        @param task_description 任务描述
        @return 任务步骤列表
        """
        prompt_template = """你是一个任务规划助手，负责将复杂任务分解为可执行的步骤。

【重要约束】
- 禁止使用 Agent Team（team 命令）
- 禁止委托给其他 Agent
- 这是单 Agent 直接执行计划，所有步骤必须由当前 Agent 自己完成
- **重要**: 请只输出步骤列表，不要输出任何代码、代码片段或实现细节

用户任务：{task_description}

请将上述任务分解为 3-8 个具体的执行步骤，规则：
1. 每个步骤是一个独立的具体动作，由当前 Agent 使用工具完成
2. 步骤描述要清晰、具体，只描述"做什么"而不是"怎么做"
3. 按逻辑顺序排列
4. 不要包含"使用 Agent Team"或"委托"等跨 Agent 协作的步骤
5. 不要输出代码、代码片段或任何实现细节

请直接返回一个步骤列表，每行一个步骤，不要有其他解释。例如：
1. 创建项目文件夹
2. 编写 HTML 基础结构
3. 实现核心游戏逻辑
4. 添加样式和交互
5. 测试验证"""

        max_retries = 3
        last_error = ""

        for attempt in range(max_retries):
            prompt = prompt_template.format(task_description=task_description)
            messages = [{"role": "user", "content": prompt}]

            total_chars = sum(len(m["content"]) for m in messages)
            if total_chars > 50000:
                logger.warning(f"[PlanMode] analyze_task 消息过长 ({total_chars} chars)")

            response = await self.cli.model_adapter.chat(messages, self.cli.system_prompt)

            tasks = self._parse_task_list(response)

            # 检查解析结果是否合理
            if 1 <= len(tasks) <= 20:
                logger.info(f"[PlanMode] 解析成功，第 {attempt + 1} 次尝试，获取 {len(tasks)} 个任务")
                return tasks

            # 解析失败，记录原因
            last_error = f"尝试 {attempt + 1}: 解析出 {len(tasks)} 个任务（期望 3-8 个）"
            logger.warning(f"[PlanMode] 解析结果不合理: {last_error}")
            logger.debug(f"[PlanMode] LLM 原始输出:\n{response[:1000]}...")

            # 继续重试

        # 所有尝试都失败
        logger.error(f"[PlanMode] 所有 {max_retries} 次解析尝试都失败: {last_error}")
        return []

    def _parse_task_list(self, response: str) -> list[str]:
        """
        @brief 解析 AI 返回的任务列表
        @param response AI 响应文本
        @return 任务列表
        """
        logger.info(f"[PlanMode] 解析任务列表，response长度={len(response)}")
        logger.debug(f"[PlanMode] 任务列表响应内容:\n{response[:500]}...")

        lines = response.strip().split("\n")

        tasks = []
        task_pattern = re.compile(r'^[\d\.\)\-\*]+[\s]*(.+)')

        # 代码行检测特征
        code_indicators = [
            '{', '}', ';', '=>', '->',
            'function ', 'const ', 'let ', 'var ',
            'class ', 'import ', 'export ', 'return ',
            'if (', 'for (', 'while (',
            'background:', 'color:', 'margin:', 'padding:', 'font-', 'border:',
            'box-shadow:', 'display:', 'position:', 'width:', 'height:',
            'board[', 'ctx.', 'canvas.', 'Array(', 'document.',
            '::', ');', ');', '->', '=>', '#', '@'
        ]

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 跳过 XML/HTML 标签和 thinking 标记
            if line.startswith('<') or 'thinking' in line.lower():
                continue

            # 跳过代码行（包含常见编程语法特征）
            if any(indicator in line for indicator in code_indicators):
                continue

            match = task_pattern.match(line)
            if match:
                task_text = match.group(1).strip()
                # 更严格的长度检查：任务描述应该在 10-200 字符之间
                if task_text and 10 <= len(task_text) <= 200:
                    tasks.append(task_text)

        # 如果严格解析失败，不再使用宽松的 fallback
        # 改为保守的段落模式：只在明确不是代码的情况下才使用
        if not tasks and response.strip():
            logger.warning("[PlanMode] 标准格式解析失败，尝试段落模式")
            paragraphs = response.strip().split("\n\n")
            code_chars = ['{', '}', '=>', '->', 'function ', 'const ', 'let ']
            for para in paragraphs:
                para = para.strip()
                # 段落应该在 20-500 字符之间，且不像是代码
                if 20 <= len(para) <= 500 and not any(c in para for c in code_chars):
                    tasks.append(para)

        logger.info(f"[PlanMode] 解析出 {len(tasks)} 个任务")
        for i, task in enumerate(tasks):
            logger.info(f"[PlanMode] 任务 {i+1}: {task[:100]}...")

        return tasks

    async def execute_task(self, task_description: str) -> str:
        """
        @brief 执行单个任务
        @param task_description 任务描述
        @return 执行结果
        """
        from src.cli.rich_ui import (
            print_plan_status,
            print_output,
            print_error_output
        )

        results = []
        has_error = False
        task_index = self.get_all_tasks().index(task_description)
        self.set_current_task(task_index)

        print_plan_status(f"正在执行: {task_description}", "executing")

        try:
            async for event in self.cli.execute_task(task_description):
                logger.info(f"[PlanMode] 收到事件: {event.type}")
                if event.type == EventType.THINKING:
                    print_plan_status(event.content, "thinking")
                elif event.type == EventType.TOOL_CALL:
                    logger.info(f"[PlanMode] 工具调用事件: {event.content}")
                elif event.type == EventType.TOOL_RESULT:
                    logger.info(f"[PlanMode] 工具结果事件: {event.content[:200] if event.content else 'empty'}")
                elif event.type == EventType.OUTPUT:
                    logger.info(f"[PlanMode] 输出事件: {event.content[:200] if event.content else 'empty'}")
                    results.append(event.content)
                elif event.type == EventType.ERROR:
                    logger.error(f"[PlanMode] 错误事件: {event.content}")
                    print_error_output(event.content)
                    results.append(f"[Error] {event.content}")
                    has_error = True
                elif event.type == EventType.DONE:
                    logger.info(f"[PlanMode] DONE 事件: {event.content}")
                    # DONE 事件表示任务执行流程结束，但不一定是成功
                    if has_error:
                        logger.info(f"[PlanMode] 任务因错误中断")

            result = "\n".join(results) if results else "任务完成"
            if has_error:
                result = f"[部分失败] {result}"
        except asyncio.CancelledError:
            result = "[Cancelled] 任务被取消"
            print_plan_status("任务已取消", "warning")
            raise
        except Exception as e:
            result = f"[Error] {str(e)}"
            print_error_output(str(e))

        return result

    async def _execute_work_item(self, work_item: WorkItem) -> tuple[str, list[dict], str]:
        """Execute a single work item and return (result, tool_calls, stop_reason).

        Args:
            work_item: The work item to execute

        Returns:
            Tuple of (result string, list of tool calls, stop_reason string)
        """
        from src.cli.rich_ui import (
            print_plan_status,
            print_error_output
        )

        results = []
        tool_calls_found = []
        tool_results = []  # 收集 tool results
        has_error = False
        stop_reason = None

        try:
            async for event in self.cli.execute_task(work_item.description):
                if event.type == EventType.THINKING:
                    print_plan_status(event.content, "thinking")
                elif event.type == EventType.TOOL_CALL:
                    tool_calls_found.append(event.metadata)
                elif event.type == EventType.TOOL_RESULT:
                    # 收集 tool results 以便后续处理
                    tool_results.append(event.content)
                elif event.type == EventType.OUTPUT:
                    results.append(event.content)
                elif event.type == EventType.ERROR:
                    print_error_output(event.content)
                    results.append(f"[Error] {event.content}")
                    has_error = True
                elif event.type == EventType.DONE:
                    # 从 metadata 获取 stop_reason
                    stop_reason = event.metadata.get("stop_reason") if event.metadata else None
                    if has_error:
                        logger.info("[PlanMode] 任务因错误中断")

            result = "\n".join(results) if results else "任务完成"
            if has_error:
                result = f"[部分失败] {result}"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            result = f"[Error] {str(e)}"
            print_error_output(str(e))
            has_error = True

        # 返回时附带 has_error 状态，通过 stop_reason 传递
        # 如果有错误，stop_reason 设为 "error" 以便调用方判断
        actual_stop_reason = "error" if has_error else (stop_reason or "unknown")

        return (result, tool_calls_found, actual_stop_reason)

    async def run(self, task_description: str) -> bool:
        """
        @brief 运行完整的计划执行流程（带用户确认）
        @param task_description 任务描述
        @return 是否全部成功完成
        """
        from src.cli.rich_ui import (
            print_task_list,
            print_plan_status,
            print_plan_header,
            input_with_prompt
        )

        # 分析任务生成计划
        print_plan_status("正在分析任务...", "analyzing")

        tasks = await self.analyze_task(task_description)

        if not tasks:
            print_plan_status("无法生成任务计划，请尝试更详细地描述任务", "error")
            return False

        self.set_tasks(tasks)

        # 显示计划并等待确认
        confirmed = await self._wait_for_confirmation(tasks)

        if confirmed is None:  # 用户取消
            print_plan_status("已取消执行", "info")
            return False

        if not confirmed:
            # 用户选择重新分析
            self._reanalysis_count += 1
            if self._reanalysis_count >= 3:
                print_plan_status("已达到最大重新分析次数(3次)，请尝试更详细地描述任务", "error")
                return False
            print_plan_status(f"正在重新分析... (第 {self._reanalysis_count} 次)", "analyzing")
            return await self.run(task_description)

        # 确认后执行计划
        print_plan_header()
        logger.info(f"[run] 用户已确认，开始执行 {len(tasks)} 个任务")
        print_plan_status(f"开始执行 {len(tasks)} 个任务步骤", "completed")

        from src.agent import AgentLoop, LoopEvent
        from src.cli.rich_ui import print_task_list

        work_item_source = PlanModeWorkItemSource(self)
        self._work_item_source = work_item_source

        # Access the loop instance via closure
        _loop = None

        async def on_iteration_start(iteration: int):
            """Called when an iteration starts - show task list."""
            logger.info(f"[on_iteration_start] ====== 迭代开始 ======")
            logger.info(f"[on_iteration_start] iteration={iteration}")
            nonlocal _loop
            if _loop is None:
                logger.info("[on_iteration_start] _loop is None，返回")
                return
            item = _loop.current_work_item
            logger.info(f"[on_iteration_start] current_work_item={item}")
            if item is None:
                logger.info("[on_iteration_start] item is None，返回")
                return
            task_index = int(item.id)
            logger.info(f"[on_iteration_start] task_index={task_index}")
            logger.info(f"[on_iteration_start] completed_indices={self.get_completed_indices()}")
            self.set_current_task(task_index)
            print()
            print_task_list(self.get_all_tasks(), task_index, self.get_completed_indices())

        async def on_iteration_end(iteration: int, success: bool, elapsed: float):
            """Called when an iteration ends."""
            logger.info(f"[on_iteration_end] iteration={iteration}, success={success}, elapsed={elapsed}s")

        loop = AgentLoop(
            work_item_source=work_item_source,
            on_iteration_start=on_iteration_start,
            on_iteration_end=on_iteration_end,
            on_confirmation_check=self._plan_confirmation_check,
        )
        _loop = loop

        async def execute_fn():
            """Execute the current work item."""
            result, tool_calls, stop_reason = await self._execute_work_item(loop.current_work_item)
            return (result, tool_calls, stop_reason)

        try:
            logger.info(f"[execute_plan] ========== 开始执行计划 ==========")
            logger.info(f"[execute_plan] 总任务数: {len(self.tasks)}")
            for i, task in enumerate(self.tasks):
                logger.info(f"[execute_plan]   任务 {i}: {task.description[:80]}...")
            await loop.run(execute_fn)
            logger.info(f"[execute_plan] loop.run() 返回")
            logger.info(f"[execute_plan] is_all_completed={self.is_all_completed()}")
        except asyncio.CancelledError:
            print_plan_status("计划执行已被取消", "warning")
            return False

        if self.is_all_completed():
            print_plan_status("🎉 所有任务已完成！", "completed")
            return True
        else:
            print_plan_status("部分任务未完成", "error")
            logger.info(f"[execute_plan] Not all completed, current_index={self._work_item_source._current_index if hasattr(self, '_work_item_source') else 'N/A'}")
            return False

    async def _wait_for_confirmation(self, tasks: list[str]) -> Optional[bool]:
        """等待用户确认计划

        Returns:
            True - 确认执行
            False - 重新分析
            None - 取消
        """
        # 显示计划确认界面
        logger.info(f"[_wait_for_confirmation] 调用 print_plan_confirmation，显示 {len(tasks)} 个任务")
        print_plan_confirmation(tasks)

        while True:
            choice = input_with_prompt("请输入 (y/n/e/s): ").strip().lower()

            if choice in ('y', 'yes', '是'):
                return True
            elif choice in ('n', 'no', '否', 'c', 'cancel'):
                return None
            elif choice in ('e', 'edit', '重新分析'):
                return False
            elif choice in ('s', 'show', '显示'):
                print_plan_confirmation(tasks)
            else:
                console.print("[yellow]无效输入，请输入 y/n/e/s[/yellow]")

    async def _plan_confirmation_check(self, response: str, stop_reason: str) -> Optional[bool]:
        """Plan mode 的任务完成确认

        Plan mode 不需要额外的 LLM 确认，因为 execute_task 内部已经做了确认检查。
        我们只需要检查是否有执行错误。

        Args:
            response: LLM 响应文本（来自 execute_task）
            stop_reason: 停止原因，如果是 "error" 表示任务执行失败

        Returns:
            True - 确认完成（无错误，任务成功）
            False - 未确认完成（任务失败）
            None - 跳过确认
        """
        # 如果 stop_reason 是 "error"，说明任务执行失败，不标记为完成
        if stop_reason == "error":
            logger.info(f"[PlanMode] 任务执行失败 (stop_reason={stop_reason})，不标记为完成")
            return False

        # 没有错误，说明任务成功完成
        # execute_task 内部的确认检查已经验证过模型说"任务完成"
        logger.info(f"[PlanMode] 任务确认完成（无错误），将标记为完成")
        return True


__all__ = ["PlanModeManager", "PlanModeWorkItemSource", "TaskItem"]
