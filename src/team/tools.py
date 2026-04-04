"""Team Tool - Tool for LLM to create and manage teams"""
import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.tools.registry import Tool

from .database import Database
from .event_bus import EventBus
from .models import MessageType
from .manager import TeamManager
from .message_bus import MessageBus
from .storage import TeamStorage
from .tracker import RequestTracker
from .protocol_handler import ProtocolHandler
from .protocol_tools import ProtocolTools
from .teammate import Teammate
from .monitor import TeamMonitor
from .task_board import TaskBoard
from .worktree_manager import WorktreeManager
from src.utils import get_logger
from src.cli.rich_ui import console
from rich.live import Live
from rich.table import Table
from rich.box import ROUNDED
from rich.layout import Layout
from rich.panel import Panel
from dataclasses import dataclass, field
from typing import Optional

logger = get_logger("team.tools")


@dataclass
class MemberProgress:
    """Progress info for a single member"""
    progress: int = 0
    current_action: str = ""
    completed: list = field(default_factory=list)
    remaining: list = field(default_factory=list)
    last_update: float = 0.0


@dataclass
class TaskBoardSnapshot:
    """Cached task board state"""
    tasks: list = field(default_factory=list)
    pending: int = 0
    in_progress: int = 0
    completed: int = 0
    total: int = 0
    last_update: float = 0.0


@dataclass
class TeamPanelState:
    """Unified state for the real-time team panel"""
    team_name: str
    members: dict[str, MemberProgress] = field(default_factory=dict)
    task_board_snapshot: Optional[TaskBoardSnapshot] = None
    last_render_time: float = 0.0
    last_throttle_time: float = 0.0


class TeamTool:  # Inherits from Tool at runtime via registration
    """Tool for creating and managing Agent Teams

    This tool allows the main agent (Lead) to:
    - Create teams with multiple members
    - Send messages between members
    - Query team status
    - Coordinate parallel execution
    - Handle timeout and degradation
    - Approve/reject teammate plans
    - Track shutdown and plan approval requests
    """

    def __init__(self):
        self.storage = TeamStorage()
        self.message_bus = MessageBus(self.storage)
        self.manager = TeamManager(self.storage, self.message_bus)
        self.tracker = RequestTracker()
        self.protocol_handler = ProtocolHandler(self.tracker, self.message_bus)
        self.protocol_tools = ProtocolTools(self.tracker, self.message_bus)
        self._active_teammates: dict[str, Teammate] = {}
        self._task_boards: dict[str, TaskBoard] = {}
        self._event_buses: dict[str, EventBus] = {}
        self._worktree_managers: dict[str, WorktreeManager] = {}
        self._member_worktrees: dict[str, str] = {}
        self._work_roots: dict[str, str] = {}
        self._monitor: Optional[TeamMonitor] = None
        self._panel_state: dict[str, TeamPanelState] = {}
        self._live_instance: Optional[Live] = None
        self._team_name_for_live: Optional[str] = None
        self._THROTTLE_INTERVAL: float = 1.0  # Minimum seconds between renders
        self._TASK_BOARD_CACHE_TTL: float = 5.0  # Task board cache TTL

    @property
    def name(self) -> str:
        return "team"

    @property
    def description(self) -> str:
        return """Agent Team 协作工具 - 用于创建和管理多 Agent 团队（仅支持自主模式）

支持以下操作：

**create** - 创建空团队（用于自主模式）
- team_name: 团队名称
- work_root: 工作根目录（Agent 将在此目录的 Git Worktree 中工作）
- 创建后会返回工作流程待办清单

**send** - 向指定成员发送消息
- team_name: 团队名称
- to: 收信人名称
- content: 消息内容
- type: 可选，消息类型 (message/status/result)

**broadcast** - 广播消息给所有成员
- team_name: 团队名称
- content: 消息内容

**status** - 查询团队状态
- team_name: 团队名称

**shutdown** - 关闭团队
- team_name: 团队名称

**await** - 等待成员完成或超时
- team_name: 团队名称
- timeout: 超时时间（秒）

**approve** - 审批 Teammate 的计划或检查请求状态
- team_name: 团队名称
- type: 请求类型 ("shutdown" 或 "plan")
- request_id: 请求 ID
- approve: 可选，是否批准 (true/false)
- feedback: 可选，反馈信息

**add_task** - 向任务板添加任务
- team_name: 团队名称
- subject: 任务标题
- description: 任务描述
- blocked_by: 可选，依赖的任务 ID 列表
- spec_file: 可选，共享规范文件路径（如 "D:/work/design.md"）

**list_tasks** - 列出任务板上的所有任务
- team_name: 团队名称

**generate_spec** - 生成 SPEC 规范文件
- team_name: 团队名称
- spec_content: SPEC 规范内容
- 生成的 SPEC 文件会保存并在派生成员时注入到系统提示
- 【重要】此操作会自动更新待办清单

**todo** - 管理团队工作待办清单
- team_name: 团队名称
- operation: 操作类型
  - "show": 显示当前待办清单（默认）
  - "advance": 标记当前步骤完成，移动到下一步
  - "reset": 重置待办清单

**spawn_autonomous** - 派生自主成员
- team_name: 团队名称
- role: 成员角色（固定为 "developer"）
- 系统会自动生成成员名称：member1, member2, member3...
- 如果在 Git 仓库中，会自动为该成员创建专属的 Git Worktree

**worktree_create** - 创建 Git Worktree
- team_name: 团队名称
- name: worktree 名称
- task_id: 可选，绑定的任务 ID
- base_ref: 可选，基准分支 (默认: HEAD)

**worktree_list** - 列出所有 Worktree
- team_name: 团队名称

**worktree_bind** - 绑定任务到 Worktree
- team_name: 团队名称
- name: worktree 名称
- task_id: 任务 ID

**worktree_run** - 在 Worktree 中执行命令
- team_name: 团队名称
- name: worktree 名称
- command: 要执行的命令

**worktree_remove** - 删除 Worktree
- team_name: 团队名称
- name: worktree 名称
- force: 可选，强制删除 (默认: false)

**worktree_events** - 查看 Worktree 相关事件
- team_name: 团队名称
- limit: 可选，事件数量限制 (默认: 20)
- event_type: 可选，事件类型过滤
- task_id: 可选，任务 ID 过滤

## 工作流程

```
# 1. 创建团队（会自动创建待办清单）
team(action="create", team_name="my-team", work_root="D:/Development/Other")
# → 查看返回的待办清单，下一步是"生成 SPEC 规范"

# 2. 生成 SPEC 规范（必须首先完成）
team(action="generate_spec", team_name="my-team", spec_content="项目的完整设计规范...")
# → 系统自动更新待办清单

# 3. 添加任务到任务板（基于 SPEC）
team(action="add_task", team_name="my-team", subject="任务1", description="...")
team(action="add_task", team_name="my-team", subject="任务2", description="...", blocked_by=[1])

# 4. 派生自主成员（成员会自动获得 SPEC 内容）
team(action="spawn_autonomous", team_name="my-team", name="coder1", role="developer")
team(action="spawn_autonomous", team_name="my-team", name="coder2", role="developer")

# 5. 查看/管理待办清单
team(action="todo", team_name="my-team", operation="show")

# 6. 成员认领任务后开始工作
# 成员使用 claim_task 工具认领任务

# 7. 成员完成工作后使用 complete_task 标记完成
# 系统会自动提交并合并到主分支

# 8. 查看任务状态
team(action="list_tasks", team_name="my-team")
```

重要约束（必须遵守）：
- Lead Agent 在团队模式下**不写代码**，只协调和沟通
- **必须首先调用 generate_spec 生成项目规范**，然后再派生成员
- **使用 todo action 查看当前步骤和下一步行动**
- 绝对不要使用 file_write 或 shell_run 在成员 worktree 中写代码
- 绝对不要使用 file_write 在 work_root 根目录写代码
- 通过 send/broadcast 消息向成员下达指令
- 成员使用 complete_task 完成工作并合并代码"""

    def get_schema(self, input_model=None) -> dict:
        """Get tool schema for model."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._get_input_schema()
        }

    def _get_input_schema(self) -> dict:
        """Get input schema for the team tool."""
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "send", "broadcast", "status", "shutdown", "await", "approve", "add_task", "list_tasks", "spawn_autonomous", "complete_task", "generate_spec", "todo", "worktree_create", "worktree_list", "worktree_bind", "worktree_run", "worktree_remove", "worktree_events"],
                    "description": "要执行的操作类型"
                },
                "team_name": {
                    "type": "string",
                    "description": "团队名称"
                },
                "work_root": {
                    "type": "string",
                    "description": "工作根目录（Agent 将在此目录的 Git Worktree 中工作。如果不是 Git 仓库会自动初始化。）"
                },
                "name": {"type": "string", "description": "成员名称（用于 worktree_*，spawn_autonomous 时自动生成）"},
                "role": {"type": "string", "description": "成员角色（用于 spawn_autonomous，固定为 developer）"},
                "to": {"type": "string", "description": "收信人名称"},
                "content": {"type": "string", "description": "消息内容"},
                "type": {"type": "string", "description": "消息类型"},
                "timeout": {"type": "number", "description": "超时时间（秒）"},
                "request_id": {"type": "string", "description": "请求 ID（用于 approve）"},
                "approve": {"type": "boolean", "description": "是否批准（用于 approve）"},
                "feedback": {"type": "string", "description": "反馈信息（用于 approve）"},
                "subject": {"type": "string", "description": "任务标题（用于 add_task）"},
                "description": {"type": "string", "description": "任务描述（用于 add_task）"},
                "blocked_by": {"type": "array", "items": {"type": "integer"}, "description": "依赖的任务 ID 列表（用于 add_task）"},
                "spec_file": {"type": "string", "description": "共享规范文件路径（用于自主模式，指定任务使用的 spec 文件，如 'D:/work/design.md'）"},
                "spec_content": {"type": "string", "description": "SPEC 规范内容（用于 generate_spec）"},
                "operation": {"type": "string", "description": "操作类型（用于 todo）：show=显示清单, advance=推进步骤, reset=重置"},
                "task_id": {"type": "integer", "description": "任务 ID（用于 worktree_create, worktree_bind, worktree_events）"},
                "base_ref": {"type": "string", "description": "基准分支（用于 worktree_create）"},
                "command": {"type": "string", "description": "要执行的命令（用于 worktree_run）"},
                "force": {"type": "boolean", "description": "强制删除（用于 worktree_remove）"},
                "limit": {"type": "integer", "description": "事件数量限制（用于 worktree_events）"},
                "event_type": {"type": "string", "description": "事件类型过滤（用于 worktree_events）"}
            },
            "required": ["action"]
        }

    async def execute(self, **kwargs: Any) -> str:
        """Execute team action"""
        action = kwargs.get("action")

        if action == "create":
            return await self._create_team(**kwargs)
        elif action == "send":
            return await self._send_message(**kwargs)
        elif action == "broadcast":
            return await self._broadcast(**kwargs)
        elif action == "status":
            return await self._get_status(**kwargs)
        elif action == "shutdown":
            return await self._shutdown_team(**kwargs)
        elif action == "await":
            return await self._await_completion(**kwargs)
        elif action == "approve":
            return await self._approve(**kwargs)
        elif action == "add_task":
            return await self._add_task(**kwargs)
        elif action == "list_tasks":
            return await self._list_tasks(**kwargs)
        elif action == "spawn_autonomous":
            return await self._spawn_autonomous(**kwargs)
        elif action == "generate_spec":
            return await self._generate_spec(**kwargs)
        elif action == "todo":
            return await self._manage_todo(**kwargs)
        elif action == "worktree_create":
            return await self._worktree_create(**kwargs)
        elif action == "worktree_list":
            return await self._worktree_list(**kwargs)
        elif action == "worktree_bind":
            return await self._worktree_bind(**kwargs)
        elif action == "worktree_run":
            return await self._worktree_run(**kwargs)
        elif action == "worktree_remove":
            return await self._worktree_remove(**kwargs)
        elif action == "worktree_events":
            return await self._worktree_events(**kwargs)
        else:
            return f"Error: Unknown action '{action}'. Valid actions: create, send, broadcast, status, shutdown, await, approve, add_task, list_tasks, spawn_autonomous, complete_task, generate_spec, todo, worktree_create, worktree_list, worktree_bind, worktree_run, worktree_remove, worktree_events"

    async def _create_team(self, **kwargs) -> str:
        """Create an empty team for autonomous mode"""
        team_name = kwargs.get("team_name")
        work_root = kwargs.get("work_root")

        if not team_name:
            return "Error: team_name is required"

        return await self._create_empty_team(team_name, work_root)

    async def _create_empty_team(self, team_name: str, work_root: str = None) -> str:
        """Create empty team for autonomous mode

        Args:
            team_name: Name of the team
            work_root: Root directory for tasks (will be initialized as git repo if not already)
        """
        try:
            from .models import TeamConfig

            logger.info(f"[TeamTool] Creating empty team '{team_name}', work_root={work_root}")

            if work_root:
                work_root_path = Path(work_root)
                if not work_root_path.exists():
                    work_root_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"[TeamTool] Created work root directory: {work_root_path}")

                git_available = self._check_git_repo(work_root_path)
                if not git_available:
                    logger.info(f"[TeamTool] Initializing git repository at {work_root_path}")
                    success, msg = self._init_git_repo(work_root_path)
                    if not success:
                        logger.warning(f"[TeamTool] Failed to initialize git repo: {msg}")
                    else:
                        logger.info(f"[TeamTool] Git repository initialized at {work_root_path}")
                else:
                    logger.info(f"[TeamTool] Git repository already exists at {work_root_path}")

                self._work_roots[team_name] = str(work_root_path)
                logger.info(f"[TeamTool] Stored work_root for team '{team_name}': {work_root_path}")

                # 注册到保护路径registry
                from src.tools.protected_paths import protected_paths
                protected_paths.add_protected_path(str(work_root_path))
                logger.info(f"[TeamTool] Registered work_root to protected paths: {work_root_path}")

            task_board = TaskBoard(team_name)
            logger.info(f"[TeamTool] Created task board for team '{team_name}'")

            team_config = TeamConfig(
                team_name=team_name,
                created_at=time.time(),
                status="running",
                members=[],
            )
            self.manager.storage.save_team_config(team_config)
            logger.info(f"[TeamTool] Saved team config for '{team_name}'")

            self._task_boards[team_name] = task_board

            # Initialize panel state for this team
            self._panel_state[team_name] = TeamPanelState(team_name=team_name)
            self._refresh_task_board_cache(team_name)

            self._monitor = TeamMonitor(
                manager=self.manager,
                message_bus=self.message_bus,
                on_degrade=self._handle_degradation,
            )
            asyncio.create_task(self._monitor.start(team_name))
            logger.info(f"[TeamTool] Started team monitor for '{team_name}'")

            # 创建待办清单
            todo = self.storage.create_team_todo(team_name)
            todo_info = self.storage.format_todo_status(todo)

            work_root_info = f" Work root: {work_root}" if work_root else ""
            logger.info(f"[TeamTool] ========== Team '{team_name}' created successfully ==========")
            return (
                f"Team '{team_name}' created (empty, for autonomous mode).{work_root_info}\n\n"
                f"{todo_info}\n\n"
                f"重要：请按顺序执行待办清单中的步骤，首先完成当前步骤。"
            )

        except Exception as e:
            logger.error(f"[TeamTool] Error creating empty team: {e}")
            return f"Error creating team: {str(e)}"

    def _check_git_repo(self, path: Path) -> bool:
        """Check if a directory is a git repository"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(path),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
            )
            return result.returncode == 0 and "true" in result.stdout.lower()
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            return False

    def _init_git_repo(self, path: Path) -> tuple[bool, str]:
        """Initialize a git repository with initial commit"""
        try:
            logger.info(f"[Git] Initializing repository at {path}")

            init_result = subprocess.run(
                ["git", "init"],
                cwd=str(path),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            logger.info(f"[Git] git init result: returncode={init_result.returncode}")

            subprocess.run(
                ["git", "config", "user.email", "agent@nexus.ai"],
                cwd=str(path),
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["git", "config", "user.name", "Nexus Agent"],
                cwd=str(path),
                capture_output=True,
                timeout=5,
            )

            result = subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "Initial commit"],
                cwd=str(path),
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            logger.info(f"[Git] git commit result: returncode={result.returncode}")
            if result.returncode == 0:
                logger.info(f"[Git] Repository initialized successfully at {path}")
                return True, "Git repository initialized with initial commit"
            logger.warning(f"[Git] Initial commit failed: {result.stderr}")
            return False, result.stderr or "Failed to create initial commit"
        except subprocess.TimeoutExpired:
            logger.warning(f"[Git] Git operation timed out at {path}")
            return False, "Git operation timed out"
        except FileNotFoundError:
            logger.warning(f"[Git] Git command not found")
            return False, "Git command not found"
        except subprocess.SubprocessError as e:
            logger.warning(f"[Git] Git error: {e}")
            return False, str(e)

    async def _start_teammate_with_task(self, teammate: Teammate, task: str) -> None:
        """Start teammate and send initial task"""
        await teammate.start()
        await self.message_bus.send_task(
            teammate.team_name, teammate.name, task
        )

    async def _generate_spec(self, **kwargs) -> str:
        """Generate SPEC file in work_root

        Lead uses this to create the SPEC file before spawning members.
        The SPEC content will also be stored and injected into members' system prompts.
        """
        team_name = kwargs.get("team_name")
        spec_content = kwargs.get("spec_content", "")

        if not team_name:
            return "Error: team_name is required"

        if not spec_content:
            return "Error: spec_content is required"

        if team_name not in self._work_roots:
            return f"Error: Team '{team_name}' not found or work_root not set"

        work_root = self._work_roots[team_name]
        spec_path = Path(work_root) / "SPEC.md"

        # 添加豁免路径
        from src.tools.protected_paths import protected_paths
        protected_paths.add_exempt_path(str(spec_path))

        # 写入 SPEC 文件
        from src.tools.file import FileWriteTool
        tool = FileWriteTool()
        result = await tool.execute(file_path=str(spec_path), content=spec_content)

        # 移除豁免
        protected_paths.remove_exempt_path(str(spec_path))

        if "Successfully" in result:
            # 保存 SPEC 内容到团队存储，供后续 spawn 使用
            self.storage.save_team_spec(team_name, spec_content)
            logger.info(f"[TeamTool] SPEC generated for team '{team_name}': {spec_path}")

            # 自动更新待办清单：完成步骤2，步骤3变为 in_progress
            todo = self.storage.advance_todo_step(team_name)
            todo_info = ""
            if todo:
                todo_info = f"\n\n{self.storage.format_todo_status(todo)}"

            return f"SPEC 已生成: {spec_path}\n内容已保存，将在派生成员时注入到系统提示。{todo_info}"
        return f"Error: {result}"

    async def _manage_todo(self, **kwargs) -> str:
        """Manage team TODO - show or advance workflow steps

        Args:
            team_name: Team name
            operation: "show" (default), "advance", or "reset"
        """
        team_name = kwargs.get("team_name")
        operation = kwargs.get("operation", "show")

        if not team_name:
            return "Error: team_name is required"

        if operation == "show":
            todo = self.storage.load_team_todo(team_name)
            if not todo:
                return f"No todo found for team '{team_name}'"
            return self.storage.format_todo_status(todo)

        elif operation == "advance":
            todo = self.storage.advance_todo_step(team_name)
            if not todo:
                return f"No todo found for team '{team_name}'"
            return f"步骤已更新:\n{self.storage.format_todo_status(todo)}"

        elif operation == "reset":
            todo = self.storage.create_team_todo(team_name)
            return f"待办清单已重置:\n{self.storage.format_todo_status(todo)}"

        else:
            return f"Error: Unknown operation '{operation}'. Valid: show, advance, reset"

    async def _spawn_autonomous(self, **kwargs) -> str:
        """Spawn an autonomous member that polls the task board

        The member will idle and poll the task board for unclaimed tasks.
        If work_root is configured and is a Git repository, a dedicated Worktree will be created for this member.
        """
        team_name = kwargs.get("team_name")
        role = kwargs.get("role")

        if not all([team_name, role]):
            return "Error: team_name and role are required"

        # Auto-generate member name as member1, member2, etc.
        team_config = self.manager.get_team_config(team_name)
        existing_count = len(team_config.members) if team_config else 0
        name = f"member{existing_count + 1}"

        logger.info(f"[TeamTool] ========== Spawning autonomous member '{name}' in team '{team_name}' (role={role}) ==========")

        if team_name not in self._task_boards:
            self._task_boards[team_name] = TaskBoard(team_name)

        task_board = self._task_boards[team_name]

        worktree_info = ""
        worktree_path = None
        work_root = self._work_roots.get(team_name)

        logger.info(f"[Worktree] Preparing worktree for member '{name}': work_root={work_root}")

        if work_root:
            work_root_path = Path(work_root)
            logger.info(f"[Worktree] work_root for team '{team_name}': {work_root}")
            if work_root_path.exists() and self._check_git_repo(work_root_path):
                try:
                    result = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=str(work_root_path),
                        capture_output=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=5,
                    )
                    if result.returncode != 0:
                        logger.info(f"[Worktree] No HEAD found in {work_root}, creating initial commit...")
                        self._init_git_repo(work_root_path)
                    else:
                        logger.info(f"[Worktree] HEAD exists at {work_root}: {result.stdout.strip()}")

                    wt_name = f"member-{name}"
                    worktree_dir = work_root_path / wt_name
                    logger.info(f"[Worktree] Creating worktree for member '{name}': branch={wt_name}, path={worktree_dir}")

                    result = subprocess.run(
                        ["git", "worktree", "add", "-b", wt_name, str(worktree_dir)],
                        cwd=str(work_root_path),
                        capture_output=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=30,
                    )

                    logger.info(f"[Worktree] Git command result: returncode={result.returncode}")
                    logger.info(f"[Worktree] Git command stdout: {result.stdout}")
                    if result.stderr:
                        logger.info(f"[Worktree] Git command stderr: {result.stderr}")

                    if result.returncode == 0:
                        worktree_path = str(worktree_dir)
                        worktree_info = f"\n\n你的工作目录是 Git Worktree: {worktree_path}（分支名：{wt_name}）。所有文件操作和命令都将在这个目录中执行。完成工作后请使用 complete_task 工具标记完成。"
                        self._member_worktrees[f"{team_name}:{name}"] = worktree_path
                        logger.info(f"[Worktree] SUCCESS: Created worktree '{wt_name}' at {worktree_path} for member '{name}' in {work_root_path}")

                        result_branches = subprocess.run(
                            ["git", "branch", "-a"],
                            cwd=str(work_root_path),
                            capture_output=True,
                            encoding='utf-8',
                            errors='replace',
                            timeout=5,
                        )
                        logger.info(f"[Worktree] Current branches:\n{result_branches.stdout}")

                        result_worktrees = subprocess.run(
                            ["git", "worktree", "list"],
                            cwd=str(work_root_path),
                            capture_output=True,
                            encoding='utf-8',
                            errors='replace',
                            timeout=5,
                        )
                        logger.info(f"[Worktree] Worktree list:\n{result_worktrees.stdout}")
                    else:
                        logger.warning(f"[Worktree] FAILED: Failed to create worktree: {result.stderr}")
                        # 清理已创建的目录
                        import shutil
                        if worktree_dir.exists():
                            shutil.rmtree(worktree_dir, ignore_errors=True)
                            logger.info(f"[Worktree] Cleaned up residual directory: {worktree_dir}")
                        return f"Error: Failed to create worktree for member '{name}'. Git worktree add failed: {result.stderr}. Cannot spawn member without a valid work directory."
                except Exception as e:
                    logger.warning(f"[Worktree] EXCEPTION: Failed to create worktree for member '{name}': {e}")
                    return f"Error: Failed to create worktree for member '{name}': {str(e)}. Cannot spawn member without a valid work directory."
            else:
                logger.info(f"[Worktree] Work root {work_root} is not a git repo, using it directly")
                worktree_info = f"\n\n重要：你的工作目录是: {work_root}。请直接在这个目录中创建和修改文件。"
        else:
            logger.info(f"[Worktree] No work_root configured for team '{team_name}', checking WorktreeManager")
            wm = self._get_worktree_manager(team_name)
            if wm.is_available():
                try:
                    wt_name = f"member-{name}"
                    success, wt_message = wm.create(wt_name)
                    if success:
                        import re
                        path_match = re.search(r"at (.+)$", wt_message)
                        if path_match:
                            worktree_path = path_match.group(1)

                        if worktree_path:
                            worktree_info = f"\n\n你的工作目录是 Git Worktree: {worktree_path}。所有文件操作和命令都将在这个目录中执行。"
                            self._member_worktrees[f"{team_name}:{name}"] = worktree_path
                            logger.info(f"[Worktree] Created worktree via WorktreeManager: '{wt_name}' at {worktree_path} for member '{name}'")
                    else:
                        return f"Error: Failed to create worktree via WorktreeManager for member '{name}': {wt_message}. Cannot spawn member without a valid work directory."
                except Exception as e:
                    logger.warning(f"[Worktree] Failed to create worktree via WorktreeManager for member '{name}': {e}")
                    return f"Error: Failed to create worktree via WorktreeManager for member '{name}': {str(e)}. Cannot spawn member without a valid work directory."
            else:
                return f"Error: No work_root configured and WorktreeManager is not available for team '{team_name}'. Cannot spawn member without a valid work directory."

        # Build spec file hint from task board (if any task has a spec_file)
        spec_file_hint = ""
        if task_board:
            for task_obj in task_board.get_all_tasks():
                if task_obj.spec_file:
                    spec_file_hint = f"\n\n【关键】设计规范文件位于: {task_obj.spec_file}\n你必须从该文件读取规格说明，\n不要在 worktree 中创建或修改规范文件，只读！\n所有代码实现只能写到你的 worktree 目录中。"
                    logger.info(f"[TeamTool] Spec file found for member '{name}': {task_obj.spec_file}")

        # 获取 SPEC 内容并注入到系统提示
        spec_content = ""
        if self.storage:
            spec_content = self.storage.get_team_spec(team_name) or ""

        spec_hint = ""
        if spec_content:
            spec_hint = f"""

## 设计规范 (SPEC)
【重要】在开始任何工作之前，你必须仔细阅读以下规范。所有代码实现必须严格遵守此规范。

{spec_content}
"""
            logger.info(f"[TeamTool] Injected SPEC into system prompt for member '{name}'")

        worktree_hint = "\n\n重要：你将在 Git Worktree 中工作。具体工作目录在认领任务时由 claim_task 返回。始终使用 claim_task 获取任务和确定工作目录。"
        if spec_file_hint:
            worktree_hint += spec_file_hint

        task = f"你是 '{name}'，角色: {role}。{spec_hint}你的工作是从任务板认领任务并完成它们。当没有任务时，调用 idle 工具进入空闲状态。{worktree_hint}"

        result = self.manager.spawn_member(team_name, name, role, task, [])
        if result.startswith("Error"):
            return result

        config = self.manager.get_member_config(team_name, name)
        if config:
            # 注意：不再在 complete_task 时清理 worktree
            # worktree 只在 team shutdown 时统一清理

            # Create a dedicated ProtocolTools instance for this member
            # (don't share - each member needs their own task_board/worktree_path)
            member_protocol_tools = ProtocolTools(
                tracker=self.tracker,
                message_bus=self.message_bus,
                task_board=task_board,
                worktree_path=worktree_path,
                work_root=work_root,
                member_name=name,
                team_name=team_name,
            )
            teammate = Teammate(
                config=config,
                message_bus=self.message_bus,
                protocol_tools=member_protocol_tools,
                task_board=task_board,
                on_status_update=self._handle_status_update,
                on_complete=self._handle_teammate_complete,
            )
            if worktree_path:
                teammate.worktree_path = worktree_path
                teammate.set_work_root(work_root)
            self._active_teammates[f"{team_name}:{name}"] = teammate
            asyncio.create_task(self._start_teammate_with_task(teammate, task))

            logger.info(f"[TeamTool] Member '{name}' is polling task board for work...")
            logger.info(f"[TeamTool] ========== Autonomous member '{name}' spawned successfully ==========")

            # 提示可以更新待办清单
            todo = self.storage.load_team_todo(team_name)
            todo_hint = ""
            if todo and todo.get("current_step") == 4:
                todo_hint = f"\n\n完成所有成员派生后，可使用 todo(action='advance') 更新步骤。"

            return f"Spawned autonomous member '{name}' in team '{team_name}'. This member will poll the task board for work.{worktree_info}{todo_hint}"

        return f"Error: Failed to spawn member '{name}'. Member config not found."

    def _get_worktree_manager(self, team_name: str) -> WorktreeManager:
        """Get or create WorktreeManager for a team"""
        if team_name not in self._event_buses:
            self._event_buses[team_name] = EventBus(team_name)
        if team_name not in self._worktree_managers:
            self._worktree_managers[team_name] = WorktreeManager(team_name, self._event_buses[team_name])
        return self._worktree_managers[team_name]

    async def _worktree_create(self, **kwargs) -> str:
        """Create a new git worktree"""
        team_name = kwargs.get("team_name")
        name = kwargs.get("name")
        task_id = kwargs.get("task_id")
        base_ref = kwargs.get("base_ref", "HEAD")

        if not all([team_name, name]):
            return "Error: team_name and name are required"

        manager = self._get_worktree_manager(team_name)
        success, message = manager.create(name, task_id, base_ref)

        if success and task_id and team_name in self._task_boards:
            manager.bind_task(name, task_id)
            worktree_info = manager.get(name)
            if worktree_info:
                self._task_boards[team_name].bind_worktree(
                    task_id, name, worktree_info["path"]
                )

        return message

    async def _worktree_list(self, **kwargs) -> str:
        """List all worktrees"""
        team_name = kwargs.get("team_name")

        if not team_name:
            return "Error: team_name is required"

        manager = self._get_worktree_manager(team_name)
        worktrees = manager.list_all()

        if not worktrees:
            return f"No worktrees found for team '{team_name}'."

        lines = [f"Worktrees for team '{team_name}':", ""]
        for wt in worktrees:
            status_icon = "✅" if wt["status"] == "active" else "❌"
            task_info = f" (task #{wt['task_id']})" if wt.get("task_id") else ""
            lines.append(f"  {status_icon} {wt['name']}: {wt['path']}{task_info}")
            lines.append(f"      base_ref={wt['base_ref']}, status={wt['status']}")

        return "\n".join(lines)

    async def _worktree_bind(self, **kwargs) -> str:
        """Bind a task to a worktree"""
        team_name = kwargs.get("team_name")
        name = kwargs.get("name")
        task_id = kwargs.get("task_id")

        if not all([team_name, name, task_id]):
            return "Error: team_name, name, and task_id are required"

        manager = self._get_worktree_manager(team_name)
        success, message = manager.bind_task(name, task_id)

        if success and team_name in self._task_boards:
            worktree_info = manager.get(name)
            if worktree_info:
                self._task_boards[team_name].bind_worktree(
                    task_id, name, worktree_info["path"]
                )

        return message

    async def _worktree_run(self, **kwargs) -> str:
        """Execute a command in a worktree"""
        team_name = kwargs.get("team_name")
        name = kwargs.get("name")
        command = kwargs.get("command")

        if not all([team_name, name, command]):
            return "Error: team_name, name, and command are required"

        manager = self._get_worktree_manager(team_name)
        success, output = manager.run(name, command)

        if success:
            return f"Command executed successfully:\n{output}"
        else:
            return f"Command failed:\n{output}"

    async def _worktree_remove(self, **kwargs) -> str:
        """Remove a git worktree"""
        team_name = kwargs.get("team_name")
        name = kwargs.get("name")
        force = kwargs.get("force", False)

        if not all([team_name, name]):
            return "Error: team_name and name are required"

        manager = self._get_worktree_manager(team_name)
        success, message = manager.remove(name, force)
        return message

    async def _worktree_events(self, **kwargs) -> str:
        """List recent events for worktrees"""
        team_name = kwargs.get("team_name")
        limit = kwargs.get("limit", 20)
        event_type = kwargs.get("event_type")
        task_id = kwargs.get("task_id")

        if not team_name:
            return "Error: team_name is required"

        if team_name not in self._event_buses:
            self._event_buses[team_name] = EventBus(team_name)

        event_bus = self._event_buses[team_name]
        events = event_bus.list_recent(limit, event_type, task_id)

        return event_bus.format_events(events)

    async def _add_task(self, **kwargs) -> str:
        """Add a task to the team's task board"""
        team_name = kwargs.get("team_name")
        subject = kwargs.get("subject")
        description = kwargs.get("description", "")
        blocked_by = kwargs.get("blocked_by", [])
        spec_file = kwargs.get("spec_file")

        if not all([team_name, subject]):
            return "Error: team_name and subject are required"

        if team_name not in self._task_boards:
            self._task_boards[team_name] = TaskBoard(team_name)

        task_board = self._task_boards[team_name]
        task = task_board.add_task(subject, description, blocked_by, spec_file)
        logger.info(f"[TeamTool] Task ##{task.id}: {task.subject} added to team '{team_name}' (blocked_by: {blocked_by})")

        spec_info = f" (spec: {spec_file})" if spec_file else ""
        return f"Task added: #{task.id} - {task.subject}{spec_info}"

    async def _list_tasks(self, **kwargs) -> str:
        """List all tasks on the team's task board"""
        team_name = kwargs.get("team_name")

        if not team_name:
            return "Error: team_name is required"

        if team_name not in self._task_boards:
            return f"No task board found for team '{team_name}'. Add tasks first using add_task."

        task_board = self._task_boards[team_name]
        return task_board.format_status()

    async def _send_message(self, **kwargs) -> str:
        """Send message to a member"""
        team_name = kwargs.get("team_name")
        to = kwargs.get("to")
        content = kwargs.get("content")
        msg_type = kwargs.get("type", "message")

        if not all([team_name, to, content]):
            return "Error: team_name, to, and content are required"

        return await self.message_bus.send(team_name, "lead", to, content, msg_type)

    async def _broadcast(self, **kwargs) -> str:
        """Broadcast message to all members"""
        team_name = kwargs.get("team_name")
        content = kwargs.get("content")

        if not all([team_name, content]):
            return "Error: team_name and content are required"

        team_config = self.manager.get_team_config(team_name)
        if not team_config:
            return f"Error: Team '{team_name}' not found"

        return await self.message_bus.broadcast(
            team_name, "lead", content, team_config.members
        )

    async def _get_status(self, **kwargs) -> str:
        """Get team status"""
        team_name = kwargs.get("team_name")
        if not team_name:
            return "Error: team_name is required"

        status = await self.manager.get_status(team_name)

        if self._monitor:
            states = self._monitor.get_all_states()
            if states:
                status += "\n\nMonitor States:"
                for key, state in states.items():
                    if key.startswith(f"{team_name}:"):
                        status += f"\n  - {state.member_name}: {state.state} (progress: {state.progress}%)"

        return status

    async def _shutdown_team(self, **kwargs) -> str:
        """Shutdown entire team"""
        team_name = kwargs.get("team_name")
        if not team_name:
            return "Error: team_name is required"

        logger.info(f"[TeamTool] ===== Shutting down team '{team_name}' =====")

        if self._monitor:
            await self._monitor.stop()

        for key, teammate in list(self._active_teammates.items()):
            if key.startswith(f"{team_name}:"):
                await teammate.stop()
                del self._active_teammates[key]

        result = await self.manager.shutdown_team(team_name)

        # 清理保护路径注册
        from src.tools.protected_paths import protected_paths
        if team_name in self._work_roots:
            protected_paths.remove_protected_path(self._work_roots[team_name])
            logger.info(f"[TeamTool] Removed work_root from protected paths: {self._work_roots[team_name]}")
            del self._work_roots[team_name]

        # 清理成员 worktree 路径 + 删除 worktree
        import subprocess
        failed_removals = []  # 记录失败以便通知用户
        work_root = self._work_roots.get(team_name)  # 先保存 work_root，后面还要用
        for key in list(self._member_worktrees.keys()):
            if key.startswith(f"{team_name}:"):
                wt_path = self._member_worktrees.pop(key)
                logger.info(f"[TeamTool] Removed member worktree from registry: {wt_path}")

                # git worktree remove
                if wt_path and work_root:
                    result = subprocess.run(
                        ["git", "worktree", "remove", wt_path, "--force"],
                        cwd=work_root,
                        capture_output=True,
                        encoding='utf-8',
                        errors='replace',
                        timeout=30,
                    )
                    if result.returncode != 0:
                        logger.warning(f"[TeamTool] git worktree remove failed: {result.stderr}")
                        failed_removals.append({
                            "path": wt_path,
                            "error": result.stderr
                        })
                    else:
                        logger.info(f"[TeamTool] git worktree remove succeeded for {wt_path}")

        # 确保 Live 实例被清理
        self._stop_live_instance()

        # 构建返回结果
        result_msg = result if result else ""
        if failed_removals:
            error_details = "\n".join([f"  - {f['path']}: {f['error']}" for f in failed_removals])
            result_msg += f"\n\nWarning: {len(failed_removals)} worktree(s) failed to remove:\n{error_details}"

        return result_msg

    async def _await_completion(self, **kwargs) -> str:
        """Wait for all members to complete or timeout.

        Uses variable polling interval: starts at 2 seconds, can decrease to 1 second
        when there are pending plans or degraded members, or increase up to 10 seconds
        when everything is normal.
        """
        team_name = kwargs.get("team_name")
        timeout = kwargs.get("timeout", 300)

        if not team_name:
            return "Error: team_name is required"

        team_config = self.manager.get_team_config(team_name)
        if not team_config:
            return f"Error: Team '{team_name}' not found"

        start_time = asyncio.get_event_loop().time()
        check_interval = 2  # 初始检查间隔（秒）

        while asyncio.get_event_loop().time() - start_time < timeout:
            all_done = True
            for member_name in team_config.members:
                config = self.manager.get_member_config(team_name, member_name)
                if config and config.status not in ["done", "shutdown", "idle"]:
                    all_done = False
                    break

            if all_done:
                return "All members completed their tasks."

            pending = await self.tracker.get_pending_requests(team_name)
            if pending["plan"]:
                return (f"Team has {len(pending['plan'])} pending plan approval request(s). "
                        f"Please review and approve/reject using team(action='approve', type='plan', request_id='...').")

            if self._monitor:
                states = self._monitor.get_all_states()
                degraded = [
                    s for k, s in states.items()
                    if k.startswith(f"{team_name}:") and s.state == "degraded"
                ]
                if degraded:
                    return f"Team execution stopped. {len(degraded)} member(s) degraded."

            await asyncio.sleep(check_interval)

            # 动态调整下次检查间隔
            if pending["plan"] or (self._monitor and any(
                s.state != "normal" for k, s in self._monitor.get_all_states().items()
                if k.startswith(f"{team_name}:")
            )):
                # 有问题时更频繁检查
                check_interval = 1
            else:
                # 正常情况下逐步增加间隔，最长10秒
                check_interval = min(check_interval * 1.2, 10)

        return f"Timeout after {timeout} seconds. Check team status."

    async def _approve(self, **kwargs) -> str:
        """Approve or check status of shutdown/plan requests

        Args:
            team_name: Team name
            type: Request type ("shutdown" or "plan")
            request_id: Request ID to approve/check
            approve: Optional, whether to approve
            feedback: Optional, feedback message

        Returns:
            Status string
        """
        team_name = kwargs.get("team_name")
        req_type = kwargs.get("type")
        request_id = kwargs.get("request_id")
        approve = kwargs.get("approve")
        feedback = kwargs.get("feedback", "")

        if not team_name:
            return "Error: team_name is required"
        if not req_type:
            return "Error: type is required (shutdown or plan)"
        if not request_id:
            return "Error: request_id is required"

        if req_type == "shutdown":
            if approve is None:
                result = await self.protocol_handler.get_shutdown_status(request_id)
                if "error" in result:
                    return f"Error: {result['error']}"
                return (f"Shutdown request {request_id}:\n"
                        f"  Target: {result['target']}\n"
                        f"  Status: {result['status']}\n"
                        f"  Created: {result['created_at']}")
            else:
                # Get the shutdown request to find the target teammate name
                shutdown_req = await self.protocol_handler.tracker.get_shutdown_request(request_id)
                if not shutdown_req:
                    return f"Error: Shutdown request {request_id} not found"
                teammate_name = shutdown_req.target

                # Send notification to teammate (from "lead", to teammate_name)
                result = await self.protocol_handler.handle_shutdown_response(
                    team_name, "lead", teammate_name, request_id, approve, feedback
                )

                # Actually stop the teammate if approved
                if approve:
                    teammate_key = f"{team_name}:{teammate_name}"
                    if teammate_key in self._active_teammates:
                        await self._active_teammates[teammate_key].stop()
                        logger.info(f"[TeamTool] Stopped teammate {teammate_name}")

                return result

        elif req_type == "plan":
            if approve is None:
                result = await self.protocol_handler.get_plan_status(request_id)
                if "error" in result:
                    return f"Error: {result['error']}"
                return (f"Plan request {request_id}:\n"
                        f"  From: {result['from']}\n"
                        f"  Status: {result['status']}\n"
                        f"  Plan: {result['plan']}\n"
                        f"  Feedback: {result.get('feedback', 'N/A')}")
            else:
                # Look up the plan request to get the actual teammate name
                plan_req = await self.protocol_handler.tracker.get_plan_request(request_id)
                if not plan_req:
                    return f"Error: Plan request {request_id} not found"
                teammate_name = plan_req.from_

                result = await self.protocol_handler.handle_plan_review(
                    team_name, "lead", teammate_name, request_id, approve, feedback
                )
                return result

        else:
            return f"Error: Invalid type '{req_type}'. Valid types: shutdown, plan"

    async def _handle_status_update(self, member_name: str, report) -> None:
        """Handle status update from a teammate - with throttling"""
        logger.info(f"[TeamTool] UI update for '{member_name}': progress={report.progress}%")

        team_key = self._find_team_for_member(member_name)
        if not team_key:
            return

        # Ensure panel state exists
        if team_key not in self._panel_state:
            self._panel_state[team_key] = TeamPanelState(team_name=team_key)

        state = self._panel_state[team_key]

        # Update member progress
        if member_name not in state.members:
            state.members[member_name] = MemberProgress()

        state.members[member_name].progress = report.progress
        state.members[member_name].current_action = report.current_action
        state.members[member_name].completed = report.completed
        state.members[member_name].remaining = report.remaining
        state.members[member_name].last_update = time.time()

        # Refresh task board cache periodically
        current_time = time.time()
        if (state.task_board_snapshot is None or
                current_time - state.task_board_snapshot.last_update > self._TASK_BOARD_CACHE_TTL):
            self._refresh_task_board_cache(team_key)

        # Throttle check - only render if enough time has passed
        if current_time - state.last_throttle_time >= self._THROTTLE_INTERVAL:
            self._render_panel(team_key)
            state.last_throttle_time = current_time

    async def _handle_teammate_complete(self, member_name: str, task: str) -> None:
        """Handle teammate completion"""
        logger.info(f"[TeamTool] Member '{member_name}' exited with status: done (task: {task[:50]}...)")

        # 更新 member config status
        team_key = self._find_team_for_member(member_name)
        if team_key:
            await self.manager.update_member_status(team_key, member_name, "done")

        if team_key and team_key in self._panel_state:
            state = self._panel_state[team_key]
            if member_name in state.members:
                state.members[member_name].progress = 100
                state.members[member_name].current_action = "完成"
            # Refresh task board cache
            self._refresh_task_board_cache(team_key)
            # Force render on completion
            self._render_panel(team_key)

        # 检查是否所有成员都完成，如果是则停止 Live
        if self._is_all_members_done():
            self._stop_live_instance()

    def _find_team_for_member(self, member_name: str) -> Optional[str]:
        """Find the team name for a given member"""
        for key in self._active_teammates:
            if key.endswith(f":{member_name}"):
                return key.rsplit(":", 1)[0]
        return None

    def _build_member_section(self, state: TeamPanelState) -> Table:
        """构建成员状态表格"""
        table = Table(title="[bold]Member Status[/bold]", show_header=True,
                      header_style="cyan", box=ROUNDED)
        table.add_column("Member", width=10)
        table.add_column("Progress", width=12)
        table.add_column("Status", width=30)

        for name, member in sorted(state.members.items()):
            if member.progress >= 100:
                bar = f"[green]{'█' * 10}[/green]"
                status = "[green]+ Done[/green]"
            elif member.progress > 0:
                filled = int(10 * member.progress / 100)
                bar = f"[yellow]{'█' * filled}[/yellow]{'░' * (10 - filled)}"
                action = member.current_action[:25] if member.current_action else "Working"
                status = f"[yellow]{action}[/yellow]"
            else:
                bar = f"[dim]{'░' * 10}[/dim]"
                status = "[dim]- Waiting[/dim]"

            table.add_row(name, bar, status)

        return table

    def _build_task_board_section(self, state: TeamPanelState) -> Table:
        """构建任务板状态表格"""
        table = Table(title="[bold]Task Board[/bold]", show_header=True,
                      header_style="cyan", box=ROUNDED)
        table.add_column("#", width=3)
        table.add_column("Task", width=35)
        table.add_column("Status", width=10)
        table.add_column("Owner", width=12)

        if state.task_board_snapshot:
            for task in state.task_board_snapshot.tasks:
                status = task.get("status", "pending")
                status_icon = {
                    "completed": "[green]+[/green]",
                    "in_progress": "[yellow]>[/yellow]",
                    "pending": "[ ]",
                    "blocked": "[red]X[/red]",
                }.get(status, "[?]")

                owner = task.get("owner") or "-"
                subject = task.get("subject", "")[:35]
                task_id = task.get("id", "")

                # Add progress info for in_progress tasks
                if status == "in_progress" and owner in state.members:
                    progress = state.members[owner].progress
                    owner = f"{owner} ({progress}%)"

                table.add_row(str(task_id), subject, status_icon, owner)
        else:
            table.add_row("-", "No tasks", "-", "-")

        return table

    def _build_combined_panel(self, team_name: str) -> Panel:
        """构建组合面板 - 同时显示任务板和成员状态"""
        state = self._panel_state.get(team_name)
        if not state:
            return Panel("No data", title=f"Team: {team_name}")

        # Calculate overall progress
        total_members = len(state.members)
        completed_members = sum(1 for m in state.members.values() if m.progress >= 100)
        total_tasks = state.task_board_snapshot.total if state.task_board_snapshot else 0
        completed_tasks = state.task_board_snapshot.completed if state.task_board_snapshot else 0

        # Build title with overall progress
        title = f"[cyan]Team: {team_name}[/cyan]  [{completed_tasks}/{total_tasks} tasks, {completed_members}/{total_members} members]"

        # Build task board section
        task_section = self._build_task_board_section(state)

        # Build member status section
        member_section = self._build_member_section(state)

        # Create layout
        layout = Layout()
        layout.split_column(
            Layout(task_section, size=10),  # Fixed height for task board
            Layout(member_section),          # Flexible height for members
        )

        return Panel(layout, title=title)

    def _refresh_task_board_cache(self, team_name: str) -> None:
        """Refresh the cached task board snapshot"""
        task_board = self._task_boards.get(team_name)
        if not task_board:
            return

        status = task_board.get_status()
        if team_name in self._panel_state:
            self._panel_state[team_name].task_board_snapshot = TaskBoardSnapshot(
                tasks=status.get("tasks", []),
                pending=status.get("pending", 0),
                in_progress=status.get("in_progress", 0),
                completed=status.get("completed", 0),
                total=status.get("total", 0),
                last_update=time.time()
            )

    def _render_panel(self, team_name: str) -> None:
        """Render the panel with throttled updates"""
        if team_name not in self._panel_state:
            return

        # 如果当前显示的是不同团队的状态，先停止旧的
        if (self._live_instance is not None and
                self._team_name_for_live != team_name):
            self._stop_live_instance()

        panel = self._build_combined_panel(team_name)

        if self._live_instance is None:
            # 首次创建 Live 实例
            self._live_instance = Live(
                panel,
                refresh_per_second=4,
                console=console,
                transient=False,
            )
            self._live_instance.start()
            self._team_name_for_live = team_name
        else:
            # 更新现有 Live 实例
            self._live_instance.update(panel)

        self._panel_state[team_name].last_render_time = time.time()

    def _print_team_status_panel(self, team_name: str) -> None:
        """Legacy method - redirects to new _render_panel"""
        if team_name in self._panel_state:
            self._render_panel(team_name)

    def _stop_live_instance(self) -> None:
        """停止 Live 实例"""
        if self._live_instance is not None:
            self._live_instance.stop()
            self._live_instance = None
            self._team_name_for_live = None

    def _is_all_members_done(self) -> bool:
        """检查是否所有成员都已完成"""
        if not self._panel_state:
            return True
        for state in self._panel_state.values():
            for member in state.members.values():
                if member.progress < 100:
                    return False
        return True

    async def _handle_degradation(self, member_name: str, report) -> None:
        """Handle member degradation"""
        logger.warning(f"Member {member_name} degraded. Progress: {report.progress}%")

        tasks = self._monitor.generate_takeover_tasks(member_name, report)
        logger.info(f"Generated {len(tasks)} takeover tasks for {member_name}")

    @staticmethod
    def register_to_registry(registry):
        """Register this tool to a registry

        Args:
            registry: A ToolRegistry instance
        """
        registry.register(TeamTool())


def register_team_tool():
    """Register TeamTool to the global registry"""
    from src.tools import global_registry
    TeamTool.register_to_registry(global_registry)
