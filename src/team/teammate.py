"""Teammate - Persistent agent that executes tasks"""
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional, Callable, Awaitable, TYPE_CHECKING
from datetime import datetime

from .models import (
    TeammateConfig,
    Message,
    MessageType,
    TeammateStatus,
    StatusReport,
)
from .message_bus import MessageBus
from .storage import TeamStorage
from .task_board import TaskBoard
from src.agent.context import AgentContext, create_context
from src.adapters import ModelAdapter
from src.tools.registry import ToolRegistry, global_registry
from src.utils import get_logger

if TYPE_CHECKING:
    from .protocol_tools import ProtocolTools

logger = get_logger("team.teammate")

# 需要工作目录路径验证的工具（只限制写入操作）
PATH_REQUIRING_TOOLS = {
    "file_write",  # 创建/覆盖文件
    "file_patch",  # 修改文件
    "shell_run",   # shell 命令可以创建/修改任意位置的文件
}

# 读取类工具不需要验证 - 成员需要能读取任务板等信息
READ_ONLY_TOOLS = {"file_read", "file_search", "list_dir"}


class Teammate:
    """Persistent teammate agent with lifecycle management

    Lifecycle: INITIAL -> WORKING -> IDLE <-> WORKING -> SHUTDOWN -> DONE

    Supports autonomous mode:
    - When idle, polls task board for unclaimed tasks
    - Automatically shuts down after IDLE_TIMEOUT seconds of inactivity
    """

    POLL_INTERVAL = 5
    IDLE_TIMEOUT = 60

    def __init__(
        self,
        config: TeammateConfig,
        message_bus: MessageBus,
        adapter: Optional[ModelAdapter] = None,
        tool_registry: Optional[ToolRegistry] = None,
        protocol_tools: Optional["ProtocolTools"] = None,
        task_board: Optional[TaskBoard] = None,
        on_status_update: Optional[Callable[[str, StatusReport], Awaitable[None]]] = None,
        on_complete: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        self.config = config
        self.message_bus = message_bus
        self.adapter = adapter
        self.tool_registry = tool_registry or global_registry
        self.protocol_tools = protocol_tools
        self.task_board = task_board
        self.on_status_update = on_status_update
        self.on_complete = on_complete
        self.worktree_path: Optional[str] = None

        self.status = TeammateStatus.INITIAL
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._current_task: str = ""
        self._status_report = StatusReport()
        self._system_prompt: str = ""
        self._idle_requested: bool = False
        self._claimed_task_id: Optional[int] = None
        self._work_root: Optional[str] = None  # Set via set_work_root()

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def team_name(self) -> str:
        return self.config.team_name

    def set_work_root(self, work_root: str) -> None:
        """Set the work_root path for path replacement"""
        self._work_root = work_root

    async def start(self) -> None:
        """Start the teammate's main loop"""
        self._running = True
        await self.message_bus.register_member(self.team_name, self.name)
        await self.message_bus.send_status(
            self.team_name, self.name, "lead",
            f"Teammate {self.name} started"
        )
        logger.info(f"[Teammate:{self.name}] ===== Started in team '{self.team_name}' =====")
        self._task = asyncio.create_task(self._main_loop())

    async def stop(self) -> str:
        """Stop the teammate gracefully"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        await self.message_bus.send_status(
            self.team_name, self.name, "lead",
            f"Teammate {self.name} stopped"
        )
        await self.message_bus.unregister_member(self.team_name, self.name)
        logger.info(f"[Teammate:{self.name}] ===== Stopped =====")

        if self.on_complete:
            await self.on_complete(self.name, self._current_task)

        return f"Teammate {self.name} stopped"

    async def _main_loop(self) -> None:
        """Main event loop for the teammate with idle polling support"""
        while self._running:
            messages = await self.message_bus.receive(self.team_name, self.name)

            if not messages:
                if self.status == TeammateStatus.WORKING:
                    pass
                else:
                    self.status = TeammateStatus.IDLE
                    should_continue = await self._idle_poll()
                    if not should_continue:
                        break
                    continue

            for msg in messages:
                await self._handle_message(msg)

            if self._running and self.status != TeammateStatus.SHUTDOWN:
                self.status = TeammateStatus.WORKING

    async def _idle_poll(self) -> bool:
        """Idle polling: check inbox and task board for work

        Returns:
            True if work was found and should resume, False if should shutdown
        """
        logger.info(f"[Teammate:{self.name}] Entering idle mode, will poll for tasks...")
        polls = self.IDLE_TIMEOUT // self.POLL_INTERVAL

        for poll_count in range(polls):
            if not self._running:
                return False

            await asyncio.sleep(self.POLL_INTERVAL)

            inbox = await self.message_bus.receive(self.team_name, self.name)
            if inbox:
                logger.info(f"[Teammate:{self.name}] Wake up! Found {len(inbox)} message(s) in inbox")
                return True

            if self.task_board:
                # Use atomic scan_and_claim to prevent race conditions
                task = self.task_board.scan_and_claim(self.name)
                if task:
                    logger.info(f"[Teammate:{self.name}] Auto-claimed task ##{task.id}: {task.subject}")
                    self._claimed_task_id = task.id
                    self._current_task = f"Task #{task.id}: {task.subject}"
                    self.status = TeammateStatus.WORKING
                    await self._execute_task(
                        f"<auto-claimed>Task #{task.id}: {task.subject}\n{task.description}</auto-claimed>"
                    )
                    return True

        logger.info(f"[Teammate:{self.name}] Idle timeout, shutting down...")
        # Properly shut down before exiting
        self._running = False
        await self.message_bus.send_status(
            self.team_name, self.name, "lead",
            f"Teammate {self.name} idle timeout, shutting down"
        )
        await self.message_bus.unregister_member(self.team_name, self.name)
        logger.info(f"[Teammate:{self.name}] Stopped due to idle timeout")
        if self.on_complete:
            await self.on_complete(self.name, self._current_task)
        return False

    async def _handle_message(self, msg: Message) -> None:
        """Handle an incoming message"""
        logger.info(f"[Teammate:{self.name}] Received {msg.type} from {msg.from_}")

        if msg.type == MessageType.TASK.value:
            await self._execute_task(msg.content)
        elif msg.type == MessageType.SHUTDOWN_REQUEST.value:
            await self._handle_shutdown(msg)
        elif msg.type == MessageType.SHUTDOWN_RESPONSE.value:
            await self._handle_shutdown_response(msg)
        elif msg.type == MessageType.PLAN_APPROVAL.value:
            await self._handle_plan_approval(msg)
        elif msg.type == MessageType.PLAN_APPROVAL_RESPONSE.value:
            await self._handle_plan_approval_response(msg)
        elif msg.type == MessageType.MESSAGE.value:
            await self._execute_task(msg.content)
        elif msg.type == MessageType.WARNING.value:
            await self._handle_warning(msg)
        else:
            logger.debug(f"Teammate {self.name} ignored message type: {msg.type}")

    async def _execute_task(self, task: str) -> None:
        """Execute a task using agent loop"""
        # Replace team paths with worktree paths
        task = self._replace_paths_in_text(task)

        task_preview = task[:60].replace('\n', ' ')
        logger.info(f"[Teammate:{self.name}] Executing task: {task_preview}...")
        self._current_task = task
        self.status = TeammateStatus.WORKING

        context = self._create_context()
        self._status_report = StatusReport(
            progress=0,
            current_action=f"开始执行: {task[:50]}...",
            completed=[],
            remaining=[task],
        )

        context.add_user_message(task)

        try:
            result = await self._run_agent_loop(context)

            # 注意：complete_task 由 LLM 主动调用，不再自动调用
            # LLM 应在任务完成后调用 complete_task 工具

            if result:
                self._status_report.progress = 100
                self._status_report.current_action = "任务完成"
                self._status_report.remaining = []
                await self._report_status()

                await self.message_bus.send_result(
                    self.team_name, self.name, "lead",
                    f"Task completed by {self.name}:\n{result}"
                )
            else:
                await self.message_bus.send_result(
                    self.team_name, self.name, "lead",
                    f"Task completed by {self.name} (no output)"
                )

        except Exception as e:
            logger.error(f"Teammate {self.name} task execution error: {e}")
            if self._claimed_task_id and self.task_board:
                self.task_board.release(self._claimed_task_id)
                self._claimed_task_id = None
            await self.message_bus.send_result(
                self.team_name, self.name, "lead",
                f"Task error by {self.name}: {str(e)}"
            )

    async def _run_agent_loop(self, context: AgentContext) -> str:
        """Run the agent loop until completion or timeout.

        Uses AgentLoop for iteration control with special handling for
        the idle tool which triggers early return.
        """
        # Deferred import to avoid circular dependency
        from src.agent.loop import AgentLoop, IdleException

        max_iterations = 20
        timeout_seconds = 300.0
        start_time = time.time()

        # Track if idle was requested
        self._idle_requested = False
        idle_result_msg = ""

        loop = AgentLoop(
            context=context,
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            on_iteration_end=self._on_iteration_end,
        )

        async def execute_fn():
            """Execute one iteration: LLM call + tool execution."""
            nonlocal idle_result_msg

            # Check timeout
            if time.time() - start_time > timeout_seconds:
                logger.warning(f"Teammate {self.name} timed out after {timeout_seconds}s")
                loop.context.state.mark_error("Timeout")
                return ("", [])

            # Build tools list with protocol tools
            tools = self.tool_registry.get_tools_schema()
            if self.protocol_tools:
                protocol_schemas = self.protocol_tools.get_all_schemas()
                tools = tools + protocol_schemas

            messages = context.get_messages_for_api()
            response, tool_calls = await self.adapter.chat_with_tools(
                messages, tools, self._system_prompt
            )

            context.add_assistant_message(response)

            if not tool_calls:
                return (response, [])

            # Execute tool calls
            for tc in tool_calls:
                tool_name = tc.get("name", "")

                # Handle idle tool - raises IdleException to break out of loop
                if tool_name == "idle":
                    logger.info(f"Teammate {self.name} requested idle")
                    idle_result_msg = ("进入空闲状态。将每5秒检查任务板和收件箱，最多等待60秒。"
                                      "如果有未完成的任务，请先完成它们。")
                    context.add_tool_message(idle_result_msg, "idle")
                    # Raise IdleException to break out of the agent loop cleanly
                    # AgentLoop.run() will catch this and set loop._idle_requested = True
                    raise IdleException(idle_result_msg)

                try:
                    result = await self._execute_tool(tc)
                    context.add_tool_message(str(result), tool_name)

                    if tool_name == "claim_task":
                        task_id = tc.get("arguments", {}).get("task_id")
                        if task_id and result.startswith("成功认领"):
                            self._claimed_task_id = task_id
                except Exception as e:
                    error_result = f"Error: {str(e)}"
                    context.add_tool_message(error_result, tc.get("name", "unknown"))
                    logger.error(f"Tool execution error: {e}")

            return (response, tool_calls)

        try:
            result = await loop.run(execute_fn)
            # Check loop.idle_requested since IdleException is caught by loop.run()
            if loop.idle_requested:
                return ""
            return result
        except IdleException as e:
            logger.info(f"Teammate {self.name} entered idle state: {e.message}")
            return ""  # Return empty string for idle state
        except asyncio.TimeoutError:
            logger.warning(f"Teammate {self.name} timed out")
            return "[Timeout] Task execution timed out"
        except Exception as e:
            logger.error(f"Teammate {self.name} agent loop error: {e}")
            return f"[Error] {str(e)}"

    async def _execute_tool(self, tool_call: dict) -> str:
        """Execute a single tool call"""
        tool_name = tool_call.get("name", "")
        tool_args = tool_call.get("arguments", {})

        logger.info(f"[Teammate:{self.name}] Executing tool: {tool_name}")
        logger.info(f"[Teammate:{self.name}] Tool args: {tool_args}")

        # Set protected_paths worktree context so file_write errors report correct paths
        from src.tools.protected_paths import protected_paths
        if self.worktree_path:
            protected_paths.set_current_worktree_path(self.worktree_path)

        # Validate path for mutating tools — restrict writes to worktree only
        if tool_name in PATH_REQUIRING_TOOLS and self.worktree_path:
            validation_error = self._validate_worktree_path(tool_name, tool_args)
            if validation_error:
                logger.warning(
                    f"[Teammate:{self.name}] Path validation failed for {tool_name}: {validation_error}"
                )
                return validation_error

        # Block SPEC.md writes in worktree — only Lead Agent manages it in work_root
        if tool_name == "file_write" and self.worktree_path:
            file_path = tool_args.get("file_path", "")
            if file_path.upper().endswith("SPEC.MD"):
                logger.warning(
                    f"[Teammate:{self.name}] Blocked attempt to write SPEC.md to worktree"
                )
                return ("Error: Cannot write SPEC.md to your worktree.\n"
                        "SPEC.md is created by Lead Agent in the work_root directory.\n"
                        "Do not create or modify SPEC.md in your worktree.")

        tool = None
        if self.protocol_tools:
            tool = self.protocol_tools.get_tool(tool_name)

        if not tool:
            tool = self.tool_registry.get(tool_name)

        if not tool:
            logger.warning(f"[Teammate:{self.name}] Unknown tool: {tool_name}")
            return f"Error: Unknown tool '{tool_name}'"

        # Inject worktree isolation context for path-accepting tools
        if self.worktree_path:
            tool_args["worktree_root"] = self.worktree_path
            tool_args["cwd"] = self.worktree_path

        if hasattr(tool, "execute"):
            if hasattr(tool, "_is_protocol_tool") and tool._is_protocol_tool:
                tool_args["team_name"] = self.team_name
                tool_args["teammate_name"] = self.name
                logger.info(f"[Teammate:{self.name}] Protocol tool detected, added team_name and teammate_name")
            result = await tool.execute(**tool_args)
            logger.info(f"[Teammate:{self.name}] Tool {tool_name} result: {str(result)[:200]}")
            return str(result)
        elif hasattr(tool, "run"):
            result = await tool.run(**tool_args)
            logger.info(f"[Teammate:{self.name}] Tool {tool_name} result: {str(result)[:200]}")
            return str(result)
        else:
            logger.warning(f"[Teammate:{self.name}] Tool {tool_name} has no execute or run method")
            return f"Error: Tool '{tool_name}' has no execute or run method"

    def _create_context(self) -> AgentContext:
        """Create an isolated context for this teammate"""
        context = create_context(max_iterations=20, timeout_seconds=300.0)

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")
        time_info = f"## Current Time\nSystem time: {current_time}"

        protocol_info = """## Protocols
- Before executing major work, use the plan_approval tool to submit your plan to Lead for review
- When you receive a shutdown_request, use the shutdown_response tool to respond:
  - shutdown_response(approve=true) to approve and stop
  - shutdown_response(approve=false, reason="...") to reject and continue working
- If you have no current tasks and have finished your work, call the idle tool to enter idle state
- Use the claim_task tool to claim tasks from the task board
- 【重要】完成认领的任务后，必须调用 complete_task 工具标记完成并合并分支！
  - 任务完成后立即调用 complete_task(task_id=X)，不要等待 lead 回复

## Expected Output Format
When reporting status or completing tasks, use this format:
- Status updates: "Status: [brief description], Progress: [X]%"
- Task completion: Provide a clear summary of what was accomplished
- Errors: Describe the issue and what you tried"""

        task_board_info = ""
        if self.task_board:
            task_board_info = f"""
## Task Board
Task board directory: {self.task_board.tasks_dir}
Use list_tasks to view available tasks, use claim_task to claim a task."""

        worktree_info = ""
        if self.worktree_path:
            worktree_info = f"""
## Worktree 工作目录限制
【重要】你的工作目录是: {self.worktree_path}
- 所有文件操作（file_write, file_patch, shell_run）必须限制在这个目录内
- 禁止向 worktree 外的任何路径写入文件
- file_read 和 list_dir 可以访问任务板等必要信息"""

        full_system = f"""{time_info}

## Your Identity
You are '{self.config.name}', Role: {self.config.role}

## Your Task
{self.config.task}

## Available Tools
You can use the following tools: {', '.join(self.config.tools) if self.config.tools else 'all tools'}

{protocol_info}
{task_board_info}
{worktree_info}
"""

        self._system_prompt = full_system
        context.add_system_message(full_system)
        return context

    def make_identity_block(self) -> dict:
        """Create an identity re-injection block for context compression

        This is used after context compression to re-establish the agent's identity.

        Returns:
            A user message containing the identity information
        """
        return {
            "role": "user",
            "content": f"<identity>You are '{self.config.name}', role: {self.config.role}, team: {self.team_name}. Continue your work.</identity>"
        }

    async def _on_iteration_end(self, iteration: int, success: bool) -> None:
        """Callback at the end of each iteration"""
        progress = min(95, (iteration / 20) * 100)
        self._status_report.progress = int(progress)
        self._status_report.current_action = f"迭代 {iteration}/20"
        await self._report_status()

    def _replace_paths_in_text(self, text: str) -> str:
        """Replace team paths with worktree paths in text

        Replaces occurrences of work_root/team_name paths with the actual worktree path.
        Also replaces work_root paths that don't include team_name, BUT only for output paths.
        Input/read-only paths (like SPEC.md) are NOT replaced since they should be read from work_root.
        This ensures members use their own worktree for writing files.
        """
        if not self.worktree_path or not self._work_root or not self.config.team_name:
            return text

        work_root = Path(self._work_root)
        team_path_prefix = str(work_root / self.config.team_name)

        # Files that should NOT be replaced (read from work_root, not worktree)
        READONLY_FILES = {'SPEC.md', 'README.md', 'readme.md'}

        def replace_path(match):
            path = match.group(0)

            # Check if this is a read-only file that should stay in work_root
            path_obj = Path(path)
            if path_obj.name.upper() in READONLY_FILES:
                # Don't replace SPEC.md, README.md etc - these are read from work_root
                return path

            # Try to replace work_root/team_name path first (e.g., D:\Other\chess-game -> D:\Other\member-member1)
            if path.lower().startswith(team_path_prefix.lower()):
                remainder = path[len(team_path_prefix):]
                return self.worktree_path + remainder
            # Also replace work_root alone path (e.g., D:\Other -> D:\Other\member-member1)
            # But only if it's not already a valid worktree path
            if path.lower().startswith(str(work_root).lower()):
                # Check if this is already the worktree path
                if path.lower().startswith(self.worktree_path.lower()):
                    return path
                # Replace work_root with worktree_path
                remainder = path[len(str(work_root)):]
                return self.worktree_path + remainder
            return path

        # Match absolute Windows paths like D:\Dev\Other\project
        path_pattern = r'[A-Za-z]:\\(?:[^\\/]+\\)*[^\\/]+'
        return re.sub(path_pattern, replace_path, text)

    async def _report_status(self) -> None:
        """Send status report to lead"""
        logger.info(f"[Teammate:{self.name}] Status: progress={self._status_report.progress}%, action={self._status_report.current_action}")
        await self.message_bus.send_status(
            self.team_name, self.name, "lead",
            self._status_report.to_content()
        )

        if self.on_status_update:
            await self.on_status_update(self.name, self._status_report)

    async def _handle_shutdown(self, msg: Message) -> None:
        """Handle shutdown request

        Notifies agent to respond with shutdown_response via the protocol tools.
        Sets status to SHUTDOWN to indicate shutdown is in progress.
        """
        request_id = msg.metadata.get("request_id", "unknown")
        logger.info(f"[Teammate:{self.name}] Received shutdown request (request_id={request_id})")

        # Set status to SHUTDOWN to indicate we've received the request
        # The agent will call shutdown_response tool to complete the shutdown
        self.status = TeammateStatus.SHUTDOWN

        await self.message_bus.send_status(
            self.team_name, self.name, "lead",
            f"Received shutdown request (request_id={request_id}). "
            f"Progress: {self._status_report.progress}%. "
            f"Please respond with shutdown_response."
        )

    async def _handle_shutdown_response(self, msg: Message) -> None:
        """Handle shutdown response from teammate (actually from lead to teammate)

        When teammate calls shutdown_response tool, it goes through agent loop
        and returns here as a message from lead.
        """
        request_id = msg.metadata.get("request_id", "unknown")
        approve = msg.metadata.get("approve", False)

        if approve:
            logger.info(f"[Teammate:{self.name}] Approved shutdown (request_id={request_id})")
            self.status = TeammateStatus.SHUTDOWN
            await self.message_bus.send_result(
                self.team_name, self.name, "lead",
                f"Shutting down {self.name}. Progress: {self._status_report.progress}%"
            )
            await self.stop()
        else:
            logger.info(f"[Teammate:{self.name}] Rejected shutdown (request_id={request_id})")
            await self.message_bus.send_status(
                self.team_name, self.name, "lead",
                f"Shutdown rejected by {self.name}. Continuing work."
            )

    async def _handle_plan_approval(self, msg: Message) -> None:
        """Handle plan approval request (from teammate to lead)

        This is received by lead, not by teammate.
        Log for debugging purposes.
        """
        request_id = msg.metadata.get("request_id", "unknown")
        logger.info(f"Lead received plan approval from {self.name} (request_id={request_id})")

    async def _handle_plan_approval_response(self, msg: Message) -> None:
        """Handle plan approval response from lead

        This message contains the approval result for a previously submitted plan.
        - If approved: set status to WORKING so agent proceeds with the plan
        - If rejected: set status to IDLE so agent waits for new tasks
        """
        request_id = msg.metadata.get("request_id", "unknown")
        approve = msg.metadata.get("approve", False)
        feedback = msg.metadata.get("feedback", "")

        if approve:
            logger.info(f"[Teammate:{self.name}] Plan approved (request_id={request_id})")
            self.status = TeammateStatus.WORKING
            self._status_report.current_action = f"Plan approved (request_id={request_id})"
        else:
            logger.info(f"[Teammate:{self.name}] Plan rejected (request_id={request_id}): {feedback}")
            self.status = TeammateStatus.IDLE
            self._status_report.current_action = f"Plan rejected (request_id={request_id}): {feedback}"

        await self._report_status()

    async def _handle_warning(self, msg: Message) -> None:
        """Handle warning from lead"""
        level = msg.metadata.get("level", 1)
        logger.info(f"[Teammate:{self.name}] Received warning level {level}: {msg.content[:50]}")
        self._status_report.current_action = f"收到警告: {msg.content}"
        await self._report_status()

    # Path validation — ensures mutating operations stay within the member's worktree

    def _validate_worktree_path(self, tool_name: str, tool_args: dict) -> Optional[str]:
        """验证工具参数中的路径是否在 worktree 内"""
        worktree = Path(self.worktree_path).resolve()

        if tool_name == "shell_run":
            return self._validate_shell_path(worktree, tool_args)
        elif tool_name == "file_patch":
            return self._validate_patch_paths(worktree, tool_args)
        else:
            path_key = {"file_write": "file_path", "file_read": "file_path",
                        "file_search": "path", "list_dir": "dir_path"}.get(tool_name)
            if path_key and path_key in tool_args:
                return self._validate_single_path(worktree, tool_args[path_key], path_key)
        return None

    def _validate_single_path(self, worktree: Path, target_path: str, param_name: str) -> Optional[str]:
        """验证单个路径是否在 worktree 内"""
        try:
            resolved = Path(target_path).resolve()
            if not str(resolved).startswith(str(worktree)):
                return (f"Error: {param_name} '{target_path}' is outside your worktree.\n"
                        f"所有文件操作必须限制在你的 worktree 目录内。\n\n"
                        f"你的 worktree 路径是：{self.worktree_path}\n"
                        f"请将文件写入你的 worktree 目录。\n"
                        f"正确示例：{self.worktree_path}/game-logic.js")
        except Exception as e:
            return f"Error: Invalid path '{target_path}': {e}"
        return None

    def _validate_shell_path(self, worktree: Path, tool_args: dict) -> Optional[str]:
        """验证 shell 命令的 cwd 和命令中的路径"""
        cwd = tool_args.get("cwd")
        if cwd:
            error = self._validate_single_path(worktree, cwd, "cwd")
            if error:
                return error

        command = tool_args.get("command", "")
        invalid_paths = self._extract_paths_from_command(command, worktree)
        if invalid_paths:
            return (f"Error: shell command contains paths outside your worktree.\n"
                    f"Invalid paths: {', '.join(invalid_paths)}\n"
                    f"Your worktree: {self.worktree_path}")

        return None

    def _extract_paths_from_command(self, command: str, worktree: Path) -> list[str]:
        """Extract paths from shell command, return paths outside worktree"""
        invalid = []

        def is_path_outside_worktree(path_str: str) -> bool:
            """Check if a path is outside the worktree"""
            if not path_str or path_str.startswith('-'):
                return False
            # Skip Windows command flags like /d, /c, /r, /a, /b etc.
            # These are single-letter flags, not paths
            if re.match(r'^/[a-zA-Z]$', path_str):
                return False
            # Skip Windows cd flags like /d followed by a path (e.g., "/d D:\path")
            # The flag and path are extracted separately; skip the flag part
            if re.match(r'^/[a-zA-Z]$', path_str.strip()):
                return False
            try:
                # 移除引号进行路径检查
                path_str = path_str.strip('"').strip("'")
                p = Path(path_str).resolve()
                # 使用小写比较，确保 Windows 下大小写不敏感路径也能正确匹配
                resolved_str = str(p).lower().replace('\\', '/')
                worktree_str = str(worktree).lower().replace('\\', '/')
                return not resolved_str.startswith(worktree_str)
            except:
                return False

        def add_invalid_path(path_str: str) -> None:
            """Add path to invalid list if outside worktree"""
            if path_str and is_path_outside_worktree(path_str) and path_str not in invalid:
                invalid.append(path_str)

        # 移除引号统一处理（避免引号干扰）
        command_cleaned = command.replace('"', ' ').replace("'", ' ')

        # Pattern 1: Flag-prefixed paths (--output=path, -o path, etc.)
        flag_path_patterns = [
            r'--?(?:output|o|file|f|dir|d|cwd)[=\s]+([^\s]+)',
            r'>\s*([^\s]+)',
            r'<\s*([^\s]+)',
        ]
        for pattern in flag_path_patterns:
            for match in re.findall(pattern, command_cleaned):
                add_invalid_path(match)

        # Pattern 2: Windows 绝对路径 (C:\path 或 C:/path) - 关键修复
        # 原来使用 [A-Za-z]:[^\s\\]+ 但在遇到 \ 后停止匹配
        windows_path_pattern = r'([A-Za-z]:[/\\][^\s]+)'
        for match in re.findall(windows_path_pattern, command):
            add_invalid_path(match)

        # Pattern 3: Unix 绝对路径 (/path)
        unix_path_pattern = r'(?:^|\s)(/[^/\s]+/?[^\s]*)(?=\s|$)'
        for match in re.findall(unix_path_pattern, command):
            add_invalid_path(match)

        # Pattern 4: Multi-target copy/move commands (cp src1 src2 dest, mv src dest)
        multi_target_match = re.search(r'\b(?:cp|mv|rsync)\s+(.+?)\s+([^\s]+)$', command_cleaned)
        if multi_target_match:
            sources = multi_target_match.group(1).split()
            dest = multi_target_match.group(2)
            for src in sources:
                if src and not src.startswith('-'):
                    add_invalid_path(src)
            add_invalid_path(dest)

        # Pattern 5: rsync with various flags
        rsync_match = re.search(r'\brsync\s+(.+?)\s+([^\s]+)$', command_cleaned)
        if rsync_match:
            sources = [s.strip() for s in rsync_match.group(1).split() if s.strip()]
            dest = rsync_match.group(2)
            for src in sources:
                if src and not src.startswith('-'):
                    add_invalid_path(src)
            add_invalid_path(dest)

        return invalid

    def _validate_patch_paths(self, worktree: Path, tool_args: dict) -> Optional[str]:
        """验证 file_patch 中的路径"""
        patch = tool_args.get("patch", "")
        paths = re.findall(r'\*\*\*\s*(?:Add|Update|Delete)\s+File:\s*([^\n]+)', patch)
        for path in paths:
            error = self._validate_single_path(worktree, path.strip(), "file_path in patch")
            if error:
                return error
        return None

    # =================================================

    def get_progress_report(self) -> StatusReport:
        """Get current progress report"""
        return self._status_report
