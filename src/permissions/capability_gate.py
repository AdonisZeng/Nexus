"""Capability Permission Gate - 统一权限门控

整合 PermissionChecker 和 MCPToolApproval 的功能，
为所有工具 (native + MCP) 提供统一的权限检查接口。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from src.permissions.checker import PermissionChecker, PermissionResult
from src.permissions.registry import HIGH_RISK_PREFIXES, WRITE_PREFIXES, READ_PREFIXES

if TYPE_CHECKING:
    pass

logger = logging.getLogger("permissions.capability_gate")

# Type alias
AskUserCallback = Callable[[str, dict], Awaitable[bool]]


@dataclass
class CapabilityIntent:
    """标准化的工具调用意图"""
    source: str                    # "native" 或 "mcp"
    server: Optional[str]          # MCP 服务器名 (如果是 MCP 工具)
    tool: str                      # 工具名称 (不含前缀)
    risk: str                      # "read", "write", "high"
    raw_name: str                  # 原始工具名


class CapabilityPermissionGate:
    """统一权限门控

    整合 PermissionChecker 和 MCPToolApproval 的功能，
    为所有工具 (native + MCP) 提供统一的权限检查接口。

    设计原则:
    1. MCP 工具不绕过权限系统
    2. 使用 CapabilityIntent 标准化 intent
    3. 风险评估基于工具名前缀
    """

    def __init__(
        self,
        permission_checker: Optional[PermissionChecker] = None,
        ask_user_callback: Optional[AskUserCallback] = None,
    ):
        """初始化权限门控

        Args:
            permission_checker: 现有的 PermissionChecker 实例
            ask_user_callback: 用户确认回调 (ASK 模式)
        """
        self._permission_checker = permission_checker
        self._ask_user_callback = ask_user_callback

    def normalize(self, tool_name: str, tool_input: dict = None) -> CapabilityIntent:
        """标准化工具调用意图

        Args:
            tool_name: 工具名称 (可能带 mcp__ 前缀)
            tool_input: 工具输入参数

        Returns:
            CapabilityIntent - 标准化的意图
        """
        tool_input = tool_input or {}

        # 解析 MCP 工具名
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            if len(parts) >= 3:
                server = parts[1]
                actual_tool = parts[2]
                source = "mcp"
            else:
                server = None
                actual_tool = tool_name
                source = "native"
        else:
            server = None
            actual_tool = tool_name
            source = "native"

        lowered = actual_tool.lower()

        # 风险评估
        if actual_tool == "read_file" or lowered.startswith(READ_PREFIXES):
            risk = "read"
        elif lowered.startswith(HIGH_RISK_PREFIXES):
            risk = "high"
        elif lowered.startswith(WRITE_PREFIXES):
            risk = "write"
        else:
            # 默认: 未知工具视为写操作
            risk = "write"

        return CapabilityIntent(
            source=source,
            server=server,
            tool=actual_tool,
            risk=risk,
            raw_name=tool_name,
        )

    def check(self, intent: CapabilityIntent) -> PermissionResult:
        """检查权限

        使用 PermissionChecker 进行检查，
        尊重当前的 PermissionMode 设置。

        Args:
            intent: 标准化的工具调用意图

        Returns:
            PermissionResult
        """
        if self._permission_checker is None:
            # 无 permission_checker 时，根据风险等级决定
            if intent.risk == "high":
                return PermissionResult(
                    allowed=False,
                    reason=f"High-risk tool '{intent.tool}' requires approval",
                    mode_applied="capability_gate_default",
                    needs_confirmation=True,
                )
            return PermissionResult(
                allowed=True,
                reason="No permission checker, allowing by default",
                mode_applied="capability_gate_default",
            )

        # 委托给 PermissionChecker
        qualified_name = intent.raw_name
        return self._permission_checker.check(qualified_name)

    def check_with_fallback(
        self,
        tool_name: str,
        tool_input: dict = None,
        mcp_approval_callback: Optional[Callable] = None,
    ) -> PermissionResult:
        """带 MCP approval 回退的权限检查

        优先使用 PermissionChecker，
        如果是 MCP 工具且无 PermissionChecker 结果，则回退到 MCP approval 策略。

        Args:
            tool_name: 工具名称
            tool_input: 工具输入
            mcp_approval_callback: 可选的 MCP approval 检查回调

        Returns:
            PermissionResult
        """
        intent = self.normalize(tool_name, tool_input)
        result = self.check(intent)

        # 如果 PermissionChecker 允许，检查是否需要 MCP 层面的审批
        if result.allowed and intent.source == "mcp" and mcp_approval_callback:
            mcp_decision = mcp_approval_callback(intent.server, intent.tool, tool_input)
            if not mcp_decision.allowed:
                return PermissionResult(
                    allowed=False,
                    reason=f"MCP server '{intent.server}' denied: {mcp_decision.reason}",
                    mode_applied="mcp_approval",
                    needs_confirmation=True,
                )

        return result

    async def ask_user(
        self,
        intent: CapabilityIntent,
        tool_input: dict = None,
    ) -> bool:
        """请求用户确认

        Args:
            intent: 标准化的工具调用意图
            tool_input: 工具输入

        Returns:
            用户是否确认
        """
        tool_input = tool_input or {}

        if self._ask_user_callback:
            return await self._ask_user_callback(intent.raw_name, tool_input)

        # 默认交互式确认
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        source = (
            f"{intent.source}:{intent.server}/{intent.tool}"
            if intent.server
            else f"{intent.source}:{intent.tool}"
        )
        print(f"\n  [Permission] {source} risk={intent.risk}: {preview}")
        try:
            answer = input("  Allow? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    def set_permission_checker(self, checker: PermissionChecker) -> None:
        """设置 PermissionChecker"""
        self._permission_checker = checker

    def set_ask_user_callback(self, callback: AskUserCallback) -> None:
        """设置用户确认回调"""
        self._ask_user_callback = callback
