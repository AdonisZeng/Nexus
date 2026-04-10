"""Subagent lifecycle hooks -参照 doc/Hook.py 示例实现"""
import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from src.utils import get_logger

logger = get_logger("subagent.hooks")

# Hook 事件类型
HOOK_EVENTS = ("iteration_start", "tool_call_start", "tool_call_end", "terminated")

# Hook 超时时间（秒）
HOOK_TIMEOUT = 30

# 信任标记文件路径
TRUST_MARKER = Path.home() / ".nexus" / "trusted"


@dataclass
class HookDefinition:
    """Hook 定义，来自 frontmatter 配置"""
    command: str
    matcher: Optional[str] = None  # tool name filter, "*" = all, None = all

    @classmethod
    def from_dict(cls, data: dict) -> "HookDefinition":
        """从字典创建 HookDefinition"""
        return cls(
            command=data.get("command", ""),
            matcher=data.get("matcher"),
        )


@dataclass
class HookResult:
    """Hook 执行结果"""
    blocked: bool = False
    messages: list[str] = field(default_factory=list)
    updated_input: Optional[dict[str, Any]] = None

    def merge(self, other: "HookResult") -> None:
        """合并另一个 HookResult"""
        if other.blocked:
            self.blocked = True
        self.messages.extend(other.messages)
        if other.updated_input:
            if self.updated_input is None:
                self.updated_input = other.updated_input
            else:
                self.updated_input.update(other.updated_input)


class HookManager:
    """
    Hook 管理器，参照 doc/Hook.py 示例实现。

    退出码契约:
    - 0: 继续执行
    - 1: 阻止操作
    - 2: 注入消息到上下文

    支持结构化 stdout (JSON):
    - updatedInput: 更新工具输入参数
    - additionalContext: 注入到上下文的文本
    - permissionDecision: 权限覆盖
    """

    def __init__(self, hooks_config: Optional[dict[str, list[dict]]] = None):
        """
        初始化 HookManager

        Args:
            hooks_config: hook 配置字典，格式为 {event: [{command, matcher}]}
        """
        self._hooks: dict[str, list[HookDefinition]] = {event: [] for event in HOOK_EVENTS}
        if hooks_config:
            for event, hook_list in hooks_config.items():
                if event in HOOK_EVENTS and isinstance(hook_list, list):
                    self._hooks[event] = [HookDefinition.from_dict(h) for h in hook_list]

    def _check_workspace_trust(self) -> bool:
        """
        检查工作区是否受信任。

        受信任条件：
        1. TRUST_MARKER 文件存在
        2. SDK 模式（通过环境变量 NEXUS_SDK_MODE）
        """
        if os.environ.get("NEXUS_SDK_MODE"):
            return True
        return TRUST_MARKER.exists()

    def _build_env(self, event: str, context: Optional[dict[str, Any]] = None) -> dict[str, str]:
        """构建 hook 执行时的环境变量"""
        env = dict(os.environ)
        env["HOOK_EVENT"] = event

        if context:
            env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
            tool_input = context.get("tool_input", {})
            env["HOOK_TOOL_INPUT"] = json.dumps(tool_input, ensure_ascii=False)[:10000]
            if "tool_output" in context:
                env["HOOK_TOOL_OUTPUT"] = str(context["tool_output"])[:10000]
            if "iteration" in context:
                env["HOOK_ITERATION"] = str(context["iteration"])

        return env

    def _run_single_hook(
        self,
        command: str,
        env: dict[str, str],
        cwd: Optional[Path] = None
    ) -> tuple[int, str, str]:
        """
        运行单个 hook 命令。

        Args:
            command: hook 命令
            env: 环境变量字典
            cwd: 工作目录

        Returns:
            (returncode, stdout, stderr)
        """
        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd) if cwd else None,
                env=env,
                capture_output=True,
                text=True,
                timeout=HOOK_TIMEOUT,
            )
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            logger.warning(f"[HookManager] Hook timeout ({HOOK_TIMEOUT}s): {command[:50]}...")
            return -1, "", f"Timeout ({HOOK_TIMEOUT}s)"
        except Exception as e:
            logger.error(f"[HookManager] Hook error: {e}")
            return -1, "", str(e)

    def run_hooks(
        self,
        event: str,
        context: Optional[dict[str, Any]] = None,
        cwd: Optional[Path] = None
    ) -> HookResult:
        """
        执行指定事件的所有 hooks。

        Args:
            event: 事件名称 (iteration_start, tool_call_start, tool_call_end, terminated)
            context: 上下文字典
            cwd: 工作目录

        Returns:
            HookResult，包含 blocked, messages, updated_input
        """
        result = HookResult()

        # 信任检查
        if not self._check_workspace_trust():
            logger.debug("[HookManager] Workspace not trusted, skipping hooks")
            return result

        hooks = self._hooks.get(event, [])
        if not hooks:
            return result

        tool_name = context.get("tool_name", "") if context else ""
        env = self._build_env(event, context)

        for hook_def in hooks:
            # Matcher 检查
            if hook_def.matcher and hook_def.matcher != "*":
                if hook_def.matcher != tool_name:
                    continue

            if not hook_def.command:
                continue

            logger.debug(f"[HookManager] Running hook for {event}: {hook_def.command[:50]}...")
            returncode, stdout, stderr = self._run_single_hook(hook_def.command, env, cwd)

            if returncode == 0:
                # 继续执行
                if stdout.strip():
                    logger.debug(f"[HookManager] hook stdout: {stdout.strip()[:100]}")
                # 尝试解析结构化 stdout
                try:
                    hook_output = json.loads(stdout)
                    if "updatedInput" in hook_output and context:
                        result.updated_input = hook_output["updatedInput"]
                    if "additionalContext" in hook_output:
                        result.messages.extend(
                            hook_output["additionalContext"]
                            if isinstance(hook_output["additionalContext"], list)
                            else [hook_output["additionalContext"]]
                        )
                    if "permissionDecision" in hook_output:
                        logger.debug(f"[HookManager] permission_override: {hook_output['permissionDecision']}")
                except (json.JSONDecodeError, TypeError):
                    pass

            elif returncode == 1:
                # 阻止执行
                result.blocked = True
                reason = stderr.strip() or "Blocked by hook"
                logger.warning(f"[HookManager] Hook blocked: {reason[:200]}")
                # 仍然继续处理其他 hooks，但标记 blocked

            elif returncode == 2:
                # 注入消息
                msg = stderr.strip() if stderr.strip() else stdout.strip()
                if msg:
                    result.messages.append(msg)
                    logger.debug(f"[HookManager] Hook injected message: {msg[:200]}")

            else:
                # 错误（returncode == -1 或其他）
                error_msg = stderr.strip() or stdout.strip() or f"Hook failed with code {returncode}"
                logger.warning(f"[HookManager] Hook error: {error_msg[:200]}")

        return result

    def get_hooks(self, event: str) -> list[HookDefinition]:
        """获取指定事件的 hooks"""
        return self._hooks.get(event, [])


class HookRunner:
    """
    Subagent 内使用的 hook 执行器。

    提供 async 接口，在 SubagentRunner 的工具执行生命周期中调用。
    """

    def __init__(
        self,
        hooks: Optional[dict[str, list[HookDefinition]]],
        env: Optional[dict[str, str]] = None,
        cwd: Optional[Path] = None
    ):
        """
        初始化 HookRunner

        Args:
            hooks: hook 配置字典
            env: 环境变量字典
            cwd: 工作目录
        """
        self._hooks_config = hooks
        self._env = env or {}
        self._cwd = cwd
        self._manager: Optional[HookManager] = None
        if hooks:
            self._manager = HookManager(hooks)

    @property
    def is_enabled(self) -> bool:
        """是否启用了 hooks"""
        return self._manager is not None

    async def run_iteration_start(self, iteration: int = 1) -> HookResult:
        """执行 iteration_start hooks"""
        if not self._manager:
            return HookResult()
        context = {"iteration": iteration}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            "iteration_start",
            context,
            self._cwd
        )

    async def run_pre_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any]
    ) -> HookResult:
        """执行 tool_call_start hooks。"""
        if not self._manager:
            return HookResult()
        context = {"tool_name": tool_name, "tool_input": tool_input}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            "tool_call_start",
            context,
            self._cwd
        )

    async def run_post_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str
    ) -> HookResult:
        """执行 tool_call_end hooks。"""
        if not self._manager:
            return HookResult()
        context = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            "tool_call_end",
            context,
            self._cwd
        )

    async def run_terminated(self, reason: str = "") -> HookResult:
        """执行 terminated hooks"""
        if not self._manager:
            return HookResult()
        context = {"reason": reason}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            "terminated",
            context,
            self._cwd
        )


__all__ = ["HookDefinition", "HookResult", "HookManager", "HookRunner", "HOOK_EVENTS"]
