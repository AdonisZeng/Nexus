"""Protocol Tools - Tools for Teammate to respond to shutdown and submit plan approval"""
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.utils import get_logger

if TYPE_CHECKING:
    from .tracker import RequestTracker
    from .message_bus import MessageBus
    from .task_board import TaskBoard
    from src.tools.registry import Tool

logger = get_logger("team.protocol_tools")


class ShutdownResponseTool:
    """Tool for teammate to respond to shutdown requests

    Usage: teammate calls this tool when they receive a shutdown_request message.
    The tool will update the tracker and send response to lead.
    """

    def __init__(self, tracker: "RequestTracker", message_bus: "MessageBus"):
        self.tracker = tracker
        self.message_bus = message_bus

    @property
    def name(self) -> str:
        return "shutdown_response"

    @property
    def description(self) -> str:
        return """响应关闭请求。当收到 shutdown_request 时，使用此工具响应。

参数：
- request_id: 关闭请求的 ID（从 shutdown_request 消息中获取）
- approve: 是否同意关闭（true=同意关闭，false=拒绝关闭）
- reason: 可选，拒绝关闭的原因"""

    @property
    def is_mutating(self) -> bool:
        return False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "关闭请求的 ID"
                    },
                    "approve": {
                        "type": "boolean",
                        "description": "是否同意关闭 (true/false)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "可选，拒绝关闭的原因"
                    }
                },
                "required": ["request_id", "approve"]
            }
        }

    async def execute(
        self,
        team_name: str,
        teammate_name: str,
        request_id: str,
        approve: bool,
        reason: str = ""
    ) -> str:
        """Execute shutdown response

        @param team_name: Team name
        @param teammate_name: Teammate name responding
        @param request_id: Request ID from shutdown_request
        @param approve: Whether to approve shutdown
        @param reason: Optional reason for rejection
        @return: Status message
        """
        await self.tracker.update_shutdown_status(
            request_id, "approved" if approve else "rejected"
        )

        if approve:
            await self.message_bus.send(
                team_name,
                teammate_name,
                "lead",
                f"Shutdown approved by {teammate_name}. Progress saved.",
                "shutdown_response",
                metadata={"request_id": request_id, "approve": True}
            )
            logger.info(f"Teammate {teammate_name} approved shutdown (request_id={request_id})")
            return f"Shutdown approved. You can now stop gracefully."
        else:
            await self.message_bus.send(
                team_name,
                teammate_name,
                "lead",
                f"Shutdown rejected by {teammate_name}: {reason}",
                "shutdown_response",
                metadata={"request_id": request_id, "approve": False, "reason": reason}
            )
            logger.info(f"Teammate {teammate_name} rejected shutdown (request_id={request_id})")
            return f"Shutdown rejected. Reason sent to lead. Continue working."


class PlanApprovalTool:
    """Tool for teammate to submit plan for approval

    Usage: teammate calls this tool before executing major work.
    The tool will create a plan request and send it to lead.
    """

    def __init__(self, tracker: "RequestTracker", message_bus: "MessageBus"):
        self.tracker = tracker
        self.message_bus = message_bus

    @property
    def name(self) -> str:
        return "plan_approval"

    @property
    def description(self) -> str:
        return """提交计划审批。在执行重大工作前，使用此工具提交计划给 Lead 审批。

参数：
- team_name: 团队名称
- teammate_name: 你的名称
- plan: 详细的计划文本，包括目标、步骤、预期结果等"""

    @property
    def is_mutating(self) -> bool:
        return False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "团队名称"
                    },
                    "teammate_name": {
                        "type": "string",
                        "description": "你的名称"
                    },
                    "plan": {
                        "type": "string",
                        "description": "详细的计划文本"
                    }
                },
                "required": ["team_name", "teammate_name", "plan"]
            }
        }

    async def execute(
        self,
        team_name: str,
        teammate_name: str,
        plan: str
    ) -> str:
        """Execute plan submission

        @param team_name: Team name
        @param teammate_name: Teammate name submitting
        @param plan: Plan text
        @return: Status message with request_id
        """
        # Validate plan is not empty or too short
        if not plan or not plan.strip():
            return "Error: plan cannot be empty. Please provide a detailed plan for review."

        if len(plan.strip()) < 20:
            return "Error: plan is too short. Please provide a more detailed plan (at least 20 characters)."

        request_id = await self.tracker.create_plan_request(
            team_name, teammate_name, plan
        )

        await self.message_bus.send_plan_request(
            team_name, teammate_name, "lead", plan, request_id
        )

        logger.info(f"Teammate {teammate_name} submitted plan (request_id={request_id})")
        return (f"Plan submitted (request_id={request_id}). "
                f"Waiting for lead approval. Do not proceed until approved.")


class IdleTool:
    """Tool for teammate to enter idle state

    When a teammate has no more work, it can call this tool to enter idle polling mode.
    The teammate will poll the task board and inbox periodically looking for work.
    """

    def __init__(self, task_board: Optional["TaskBoard"] = None):
        self.task_board = task_board

    @property
    def name(self) -> str:
        return "idle"

    @property
    def description(self) -> str:
        return """声明进入空闲状态。当没有更多工作时调用此工具。

进入空闲状态后会：
1. 每5秒检查一次任务板，尝试抢任务
2. 检查收件箱是否有新消息
3. 最多等待60秒后自动关闭

参数：无"""

    @property
    def is_mutating(self) -> bool:
        return False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }

    async def execute(
        self,
        team_name: str,
        teammate_name: str
    ) -> str:
        """Execute idle entry

        @param team_name: Team name
        @param teammate_name: Teammate name entering idle
        @return: Status message
        """
        logger.info(f"Teammate {teammate_name} entering idle state")
        return ("进入空闲状态。将每5秒检查任务板和收件箱，最多等待60秒。"
                "使用 claim_task 工具可立即尝试抢任务。")


class CompleteTaskTool:
    """Tool for teammate to mark task complete and auto-merge branch

    When a teammate completes their work, this tool will:
    1. Commit changes to the member's branch
    2. Checkout master branch
    3. Merge the member's branch
    4. Handle conflicts if any
    """

    def __init__(
        self,
        task_board: Optional["TaskBoard"] = None,
        worktree_path: Optional[str] = None,
        work_root: Optional[str] = None,
        member_name: Optional[str] = None
    ):
        self.task_board = task_board
        self.worktree_path = worktree_path
        self.work_root = work_root
        self.member_name = member_name

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return """标记任务完成并自动合并分支。

【重要】当你认为任务已完成时，调用此工具：
- 代码已创建到你的 worktree 目录
- 功能已按要求实现
- 已完成必要的测试验证（如有）

系统会自动：
1. 将你的变更提交到你的分支
2. 切换到主分支
3. 合并你的分支到主分支
4. 如有冲突会通知 Lead Agent 处理

参数：
- task_id: 要标记完成的任务 ID（必须是已认领的任务）
  【Tip】如果不记得 task_id，可以不提供，系统会自动获取你当前正在处理的任务"""

    @property
    def is_mutating(self) -> bool:
        return False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "要标记完成的任务 ID（可选，不提供则自动获取当前任务）"
                    }
                }
            }
        }

    async def execute(
        self,
        team_name: str,
        teammate_name: str,
        task_id: int = None
    ) -> str:
        """Execute task completion and merge

        @param team_name: Team name
        @param teammate_name: Teammate name completing task
        @param task_id: Task ID to complete (optional, auto-detected if not provided)
        @return: Status message with merge results
        """
        import subprocess
        from pathlib import Path

        # Auto-detect task_id if not provided
        if not task_id and self.task_board and teammate_name:
            current_task = self.task_board.get_member_current_task(teammate_name)
            if current_task:
                task_id = current_task.id
                logger.info(f"[CompleteTask] Auto-detected task_id={task_id} for {teammate_name}")
            else:
                return f"Error: 无法自动检测当前任务。请提供 task_id 参数。"

        if not task_id:
            return f"Error: task_id is required. 请提供要完成的任务 ID。"

        logger.info(f"[CompleteTask] 任务 #{task_id} 完成流程开始，成员={teammate_name}")

        if not self.task_board:
            logger.warning(f"[CompleteTask] Task board not available")
            return "Error: Task board not available"

        task = self.task_board.get_task(task_id)
        if not task:
            logger.warning(f"[CompleteTask] Task #{task_id} not found")
            return f"Error: Task #{task_id} not found"

        if not self.worktree_path or not self.work_root:
            logger.warning(f"[CompleteTask] Worktree not configured")
            return f"Error: Worktree not configured for task completion"

        worktree_dir = Path(self.worktree_path)
        branch_name = f"member-{teammate_name}"

        # Detect the actual main branch name (may be 'master', 'main', or other)
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(self.work_root),
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=5,
        )
        main_branch = result.stdout.strip()
        if not main_branch:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(self.work_root),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
            )
            main_branch = result.stdout.strip()
        if not main_branch or main_branch == "HEAD":
            main_branch = "master"

        try:
            # Configure git user
            subprocess.run(
                ["git", "config", "user.email", "agent@nexus.ai"],
                cwd=str(worktree_dir),
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.name", f"Nexus Agent {teammate_name}"],
                cwd=str(worktree_dir),
                capture_output=True,
                timeout=5,
            )

            # Git add and commit
            subprocess.run(
                ["git", "add", "."],
                cwd=str(worktree_dir),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            commit_msg = f"Complete task #{task_id}: {task.subject}"
            result = subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=str(worktree_dir),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            if result.returncode != 0:
                if "nothing to commit" not in result.stdout:
                    logger.warning(f"[CompleteTask] Commit failed: {result.stderr}")
                    return f"Error committing changes: {result.stderr}"

            # Git checkout and merge
            result = subprocess.run(
                ["git", "checkout", main_branch],
                cwd=str(self.work_root),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(f"[CompleteTask] Checkout failed: {result.stderr}")
                return f"Error checking out {main_branch}: {result.stderr}"

            merge_msg = f"Merge {branch_name}: {task.subject}"
            result = subprocess.run(
                ["git", "merge", branch_name, "--no-ff", "-m", merge_msg],
                cwd=str(self.work_root),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )

            if result.returncode == 0:
                self.task_board.complete(task_id)
                logger.info(f"[CompleteTask] SUCCESS: 任务 #{task_id} 已完成并合并到 {main_branch}")

                # 注意：不再在这里清理 worktree
                # Worktree 只在 team shutdown 时统一清理
                # 这样成员可以继续接取下一个任务

                return (f"任务 #{task_id} 完成并成功合并到 {main_branch} 分支！\n"
                        f"你的变更已合并到主分支。\n"
                        f"worktree 保留用于下一个任务。")
            else:
                if "conflict" in result.stdout.lower() or "CONFLICT" in result.stdout:
                    logger.warning(f"[CompleteTask] 任务 #{task_id} 合并冲突")
                    subprocess.run(
                        ["git", "merge", "--abort"],
                        cwd=str(self.work_root),
                        capture_output=True,
                        timeout=10,
                    )
                    return (f"任务 #{task_id} 合并时发现冲突！\n"
                            f"冲突内容：\n{result.stdout}\n"
                            f"Lead Agent 将手动处理冲突。\n"
                            f"你的分支 {branch_name} 已保留，你可以继续其他工作。")
                else:
                    logger.warning(f"[CompleteTask] Merge failed: {result.stderr}")
                    return f"Error merging branch: {result.stderr}"

        except subprocess.TimeoutExpired:
            logger.warning(f"[CompleteTask] Git operation timed out")
            return "Error: Git operation timed out"
        except Exception as e:
            logger.error(f"[CompleteTask] Exception: {e}")
            return f"Error completing task: {str(e)}"


class ClaimTaskTool:
    """Tool for teammate to claim a task from the task board

    When a teammate is idle, it can call this tool to try to claim a specific task.
    """

    def __init__(self, task_board: Optional["TaskBoard"] = None, worktree_path: Optional[str] = None, work_root: Optional[str] = None, team_name: Optional[str] = None, member_name: Optional[str] = None):
        self.task_board = task_board
        self.worktree_path = worktree_path
        self.work_root = work_root
        self.team_name = team_name
        self.member_name = member_name

    @property
    def name(self) -> str:
        return "claim_task"

    @property
    def description(self) -> str:
        return """从任务板认领任务。指定任务 ID 尝试认领。

参数：
- task_id: 要认领的任务 ID

成功认领会返回任务详情，之后可以开始执行。
如果任务已被其他成员认领或不可用，会返回错误。"""

    @property
    def is_mutating(self) -> bool:
        return False

    def get_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "integer",
                        "description": "要认领的任务 ID"
                    }
                },
                "required": ["task_id"]
            }
        }

    async def execute(
        self,
        team_name: str,
        teammate_name: str,
        task_id: int
    ) -> str:
        """Execute task claiming

        @param team_name: Team name
        @param teammate_name: Teammate name trying to claim
        @param task_id: Task ID to claim
        @return: Status message with task details or error
        """
        logger.info(f"[ClaimTask] {teammate_name} 尝试认领任务 #{task_id}")

        if not self.task_board:
            logger.warning(f"[ClaimTask] Task board not available")
            return "Error: Task board not available"

        task = self.task_board.get_task(task_id)
        if not task:
            logger.warning(f"[ClaimTask] Task #{task_id} not found")
            return f"Error: Task #{task_id} not found"

        # ========== 检查任务依赖 ==========
        blocker_status = self.task_board.get_blocker_status(task_id)
        if not blocker_status['can_proceed']:
            blocker_list = ', '.join([f"#{b['id']}({b['subject'][:20]}...)" for b in blocker_status['blockers']])
            logger.warning(f"[ClaimTask] Task #{task_id} blocked by incomplete: {blocker_list}")
            return (f"任务 #{task_id} 被阻塞，无法认领。\n"
                    f"阻塞任务: {blocker_list}\n"
                    f"请等待阻塞任务完成后重试。")

        # 如果有依赖任务（blocked_by 非空）且全部已完成，认领前先 merge master
        if task.blocked_by and self.worktree_path and self.work_root:
            merge_error = await self._merge_master_to_worktree()
            if merge_error:
                logger.warning(f"[ClaimTask] Merge before task failed: {merge_error}")
                # 继续执行，merge 失败不阻止认领
        # =================================

        success = self.task_board.claim(task_id, teammate_name)
        if success:
            logger.info(f"[ClaimTask] {teammate_name} 成功认领任务 #{task_id}: {task.subject}")

            # Path replacement: Lead generates work_root/team_name paths,
            # but members work in work_root/member_name. Replace to match worktree.
            original_subject = task.subject
            original_description = task.description

            if self.work_root and self.worktree_path and self.team_name:
                import re
                from pathlib import Path

                # Pattern to match absolute paths like D:\Dev\Other\project or /path/project
                path_pattern = r'([A-Za-z]:(?:\\[^\s\\]+)+|/(?:[^\s/]+/)+[^\s/]+)'

                def replace_path(match):
                    path = match.group(1)
                    # Skip if this is already a member path
                    if 'member' in path.lower():
                        return path
                    # Skip short paths
                    if len(path) < 10:
                        return path

                    path_obj = Path(path)
                    if len(path_obj.parts) < 2:
                        return path

                    # Check if this path starts with work_root/team_name
                    # e.g., work_root="D:\Dev\Other", team_name="gomoku-game"
                    # path="D:\Dev\Other\gomoku-game\something" -> replace with worktree_path
                    expected_prefix = str(Path(self.work_root) / self.team_name)
                    if path.startswith(expected_prefix) or path.lower().startswith(expected_prefix.lower()):
                        # Replace the team_name part with the member name
                        # worktree_path might be D:\Dev\Other\member1
                        # We want to keep any subdirectory structure
                        remainder = path[len(expected_prefix):]
                        return self.worktree_path + remainder
                    return path

                original_subject = re.sub(path_pattern, replace_path, original_subject)
                original_description = re.sub(path_pattern, replace_path, original_description)

            worktree_note = ""
            if self.worktree_path:
                worktree_note = f"""

===== 【强制要求】你的工作目录 =====
{self.worktree_path}
===================================
【重要】此目录已存在，是你的 Git Worktree！
直接在这个目录下创建/修改代码文件即可！
不要执行 mkdir 命令创建此目录！
所有代码文件必须创建在这个目录下！
完成工作后使用 complete_task 标记完成。"""

            # Add spec file info if task has one
            spec_file_note = ""
            if task.spec_file:
                spec_file_note = f"""

===== 【只读】设计规范文件 =====
{task.spec_file}
===================================
请在开始工作前仔细阅读此文件。
不要在 worktree 中修改或创建规范文件！"""

            # 工作流程说明
            workflow_note = """
===== 【你的工作流程】=====
1. 【不要执行 mkdir】worktree 目录已存在，直接使用
2. 开始编写代码，实现任务要求
3. 测试验证功能正常
4. 调用 complete_task 标记完成

【重要】如果 file_write 报错"路径受保护"或"路径无效"，
说明你写错目录了，请切换到你的 worktree 目录重试。
============================
"""

            result = (f"成功认领任务 #{task_id}: {original_subject}\n"
                    f"描述: {original_description}{worktree_note}{spec_file_note}{workflow_note}\n"
                    f"开始执行吧！")
            return result
        else:
            logger.warning(f"[ClaimTask] {teammate_name} 认领任务 #{task_id} 失败")
            return (f"任务 #{task_id} 认领失败。"
                    f"可能已被其他成员认领，或任务状态不是 pending，或有未完成的依赖任务。")

    async def _merge_master_to_worktree(self) -> Optional[str]:
        """Merge master branch into member's worktree

        Returns:
            Error message if failed, None if success
        """
        if not self.worktree_path or not self.work_root:
            return None

        import subprocess
        from pathlib import Path

        worktree_dir = Path(self.worktree_path)
        work_root = Path(self.work_root)

        try:
            # 获取当前分支名
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=str(worktree_dir),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
            )
            branch = result.stdout.strip()
            if not branch:
                return "Cannot determine current branch"

            # 配置 git user
            subprocess.run(
                ["git", "config", "user.email", "agent@nexus.ai"],
                cwd=str(worktree_dir),
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.name", f"Nexus Agent {self.member_name}"],
                cwd=str(worktree_dir),
                capture_output=True,
                timeout=5,
            )

            # Merge master 到当前 worktree
            result = subprocess.run(
                ["git", "merge", "master", "--no-edit"],
                cwd=str(worktree_dir),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"[ClaimTask] Successfully merged master to {self.member_name}'s worktree")
                return None
            else:
                error_msg = result.stderr or result.stdout
                if "already up to date" in error_msg.lower():
                    return None
                return f"Merge failed: {error_msg}"

        except subprocess.TimeoutExpired:
            return "Git operation timed out"
        except Exception as e:
            return f"Merge exception: {str(e)}"


class ProtocolTools:
    """Container for protocol tools

    Provides access to shutdown_response, plan_approval, idle, claim_task, and complete_task tools.
    These tools need access to tracker, message_bus, and task_board from TeamTool.
    """

    def __init__(
        self,
        tracker: "RequestTracker",
        message_bus: "MessageBus",
        task_board: Optional["TaskBoard"] = None,
        worktree_path: Optional[str] = None,
        work_root: Optional[str] = None,
        member_name: Optional[str] = None,
        team_name: Optional[str] = None
    ):
        self.tracker = tracker
        self.message_bus = message_bus
        self.task_board = task_board
        self.worktree_path = worktree_path
        self.work_root = work_root
        self.member_name = member_name
        self.team_name = team_name
        self.shutdown_response = ShutdownResponseTool(tracker, message_bus)
        self.plan_approval = PlanApprovalTool(tracker, message_bus)
        self.idle = IdleTool(task_board)
        self.claim_task = ClaimTaskTool(task_board, worktree_path, work_root, team_name, member_name)
        self.complete_task = CompleteTaskTool(task_board, worktree_path, work_root, member_name)

    def get_tool(self, name: str):
        """Get tool by name"""
        tool = None
        if name == "shutdown_response":
            tool = self.shutdown_response
        elif name == "plan_approval":
            tool = self.plan_approval
        elif name == "idle":
            tool = self.idle
        elif name == "claim_task":
            tool = self.claim_task
        elif name == "complete_task":
            tool = self.complete_task

        if tool:
            tool._is_protocol_tool = True
        return tool

    def get_all_schemas(self) -> list[dict]:
        """Get schemas for all protocol tools"""
        return [
            self.shutdown_response.get_schema(),
            self.plan_approval.get_schema(),
            self.idle.get_schema(),
            self.claim_task.get_schema(),
            self.complete_task.get_schema()
        ]
