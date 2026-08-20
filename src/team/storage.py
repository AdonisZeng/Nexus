"""Team storage - File persistence for teams"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

from .models import TeamConfig, TeammateConfig, Message
from src.utils import get_logger

logger = get_logger("team.storage")


class TeamStorage:
    """Handles file persistence for teams"""

    BASE_DIR = Path.home() / ".nexus" / "teams"

    def __init__(self):
        self.BASE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_team_dir(self, team_name: str) -> Path:
        """Get team directory path"""
        return self.BASE_DIR / team_name

    def _get_members_dir(self, team_name: str) -> Path:
        """Get members directory path"""
        return self._get_team_dir(team_name) / "members"

    def _ensure_team_dirs(self, team_name: str) -> tuple[Path, Path]:
        """Ensure team and members directories exist"""
        team_dir = self._get_team_dir(team_name)
        members_dir = team_dir / "members"
        members_dir.mkdir(parents=True, exist_ok=True)
        return team_dir, members_dir

    def save_team_config(self, config: TeamConfig) -> None:
        """Save team configuration to JSON file"""
        team_dir, _ = self._ensure_team_dirs(config.team_name)
        config_path = team_dir / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved team config: {config.team_name}")

    def load_team_config(self, team_name: str) -> Optional[TeamConfig]:
        """Load team configuration from JSON file"""
        config_path = self._get_team_dir(team_name) / "config.json"
        if not config_path.exists():
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TeamConfig.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load team config {team_name}: {e}")
            return None

    def save_member_config(self, config: TeammateConfig) -> None:
        """Save member configuration to JSON file"""
        if not config.team_name:
            raise ValueError("TeammateConfig.team_name is required")
        _, members_dir = self._ensure_team_dirs(config.team_name)
        config_path = members_dir / f"{config.name}.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved member config: {config.name} in {config.team_name}")

    def load_member_config(self, team_name: str, member_name: str) -> Optional[TeammateConfig]:
        """Load member configuration from JSON file"""
        config_path = self._get_members_dir(team_name) / f"{member_name}.json"
        if not config_path.exists():
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TeammateConfig.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load member config {member_name}: {e}")
            return None

    def append_to_inbox(self, team_name: str, member_name: str, message: Message) -> None:
        """Append message to member's inbox JSONL file"""
        _, members_dir = self._ensure_team_dirs(team_name)
        inbox_path = members_dir / f"{member_name}.jsonl"
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
        logger.debug(f"Appended message to {member_name} inbox in {team_name}")

    def read_and_clear_inbox(self, team_name: str, member_name: str) -> list[Message]:
        """Read all messages from inbox and clear it"""
        inbox_path = self._get_members_dir(team_name) / f"{member_name}.jsonl"
        if not inbox_path.exists():
            return []

        messages = []
        try:
            with open(inbox_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        messages.append(Message.from_dict(data))
        except Exception as e:
            logger.error(f"Failed to read inbox {member_name}: {e}")

        inbox_path.write_text("", encoding="utf-8")
        logger.debug(f"Read and cleared {len(messages)} messages from {member_name} inbox")
        return messages

    def update_member_status(self, team_name: str, member_name: str, status: str) -> None:
        """Update member status in storage"""
        config = self.load_member_config(team_name, member_name)
        if config:
            from .models import TeammateStatus
            config.status = status
            config.last_active = time.time()
            self.save_member_config(config)

    def delete_team(self, team_name: str) -> None:
        """Delete team and all member files"""
        import shutil
        team_dir = self._get_team_dir(team_name)
        if team_dir.exists():
            shutil.rmtree(team_dir)
            logger.info(f"Deleted team: {team_name}")

    def list_teams(self) -> list[str]:
        """List all team names"""
        if not self.BASE_DIR.exists():
            return []
        return [d.name for d in self.BASE_DIR.iterdir() if d.is_dir()]

    def list_members(self, team_name: str) -> list[str]:
        """List all member names in a team"""
        members_dir = self._get_members_dir(team_name)
        if not members_dir.exists():
            return []
        return [f.stem for f in members_dir.glob("*.json")]

    def save_team_spec(self, team_name: str, spec_content: str) -> None:
        """Save team SPEC content for later retrieval by spawned members"""
        team_dir, _ = self._ensure_team_dirs(team_name)
        spec_path = team_dir / "SPEC.md"
        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(spec_content)
        logger.debug(f"Saved team SPEC for {team_name}")

    def get_team_spec(self, team_name: str) -> Optional[str]:
        """Get team SPEC content"""
        spec_path = self._get_team_dir(team_name) / "SPEC.md"
        if not spec_path.exists():
            return None
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read team SPEC for {team_name}: {e}")
            return None

    def save_team_todo(self, team_name: str, todo: dict) -> None:
        """Save team TODO to JSON file"""
        team_dir, _ = self._ensure_team_dirs(team_name)
        todo_path = team_dir / "todo.json"
        with open(todo_path, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False, indent=2)
        logger.debug(f"Saved team TODO for {team_name}")

    def load_team_todo(self, team_name: str) -> Optional[dict]:
        """Load team TODO from JSON file"""
        todo_path = self._get_team_dir(team_name) / "todo.json"
        if not todo_path.exists():
            return None
        try:
            with open(todo_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load team TODO for {team_name}: {e}")
            return None

    def create_team_todo(self, team_name: str) -> dict:
        """Create default team TODO with workflow steps"""
        todo = {
            "team_name": team_name,
            "created_at": time.time(),
            "steps": [
                {
                    "id": 1,
                    "title": "创建团队",
                    "description": "使用 create action 创建团队，设置 work_root",
                    "status": "completed"
                },
                {
                    "id": 2,
                    "title": "生成 SPEC 规范",
                    "description": "使用 generate_spec action 生成项目设计规范",
                    "status": "in_progress"
                },
                {
                    "id": 3,
                    "title": "添加任务到任务板",
                    "description": "使用 add_task action 添加任务，基于 SPEC 设计任务",
                    "status": "pending"
                },
                {
                    "id": 4,
                    "title": "派生成员",
                    "description": "使用 spawn_autonomous action 派生成员，成员会自动获得 SPEC",
                    "status": "pending"
                },
                {
                    "id": 5,
                    "title": "等待成员完成",
                    "description": "使用 await action 等待成员完成任务",
                    "status": "pending"
                },
                {
                    "id": 6,
                    "title": "合并代码",
                    "description": "确保所有 worktree 分支的代码已合并到主分支",
                    "status": "pending"
                },
                {
                    "id": 7,
                    "title": "关闭团队",
                    "description": "使用 shutdown action 关闭团队，清理 worktree",
                    "status": "pending"
                },
            ],
            "current_step": 2
        }
        self.save_team_todo(team_name, todo)
        logger.info(f"Created team TODO for {team_name}")
        return todo

    def advance_todo_step(self, team_name: str) -> dict:
        """Advance to next step: mark current as completed, next as in_progress"""
        todo = self.load_team_todo(team_name)
        if not todo:
            return None

        # Mark current step as completed
        for step in todo["steps"]:
            if step["id"] == todo["current_step"]:
                step["status"] = "completed"
                step["done_at"] = time.time()
                break

        # Find and activate next pending step
        for step in todo["steps"]:
            if step["status"] == "pending":
                step["status"] = "in_progress"
                todo["current_step"] = step["id"]
                break

        self.save_team_todo(team_name, todo)
        logger.info(f"Advanced team TODO for {team_name} to step {todo['current_step']}")
        return todo

    def format_todo_status(self, todo: dict) -> str:
        """Format TODO status as readable string"""
        lines = ["\n## 团队工作流程 - 待办清单\n"]
        for step in todo["steps"]:
            icon = {
                "completed": "✅",
                "in_progress": "🔄",
                "pending": "⏳"
            }.get(step["status"], "❓")

            current_marker = ""
            if step["id"] == todo["current_step"] and step["status"] != "completed":
                current_marker = " ← 当前步骤"

            lines.append(f"{icon} 步骤 {step['id']}: {step['title']}{current_marker}")
            if step.get("description"):
                lines.append(f"   {step['description']}")

        next_title = self._get_next_step_title(todo)
        lines.append(f"\n**下一步**: {next_title}")
        return "\n".join(lines)

    def _get_next_step_title(self, todo: dict) -> str:
        """Get title of next step to execute"""
        for step in todo["steps"]:
            if step["status"] == "in_progress":
                return f"步骤 {step['id']}: {step['title']}"
            if step["status"] == "pending":
                return f"步骤 {step['id']}: {step['title']}"
        return "全部完成"

