"""MCP 工具调用审批系统

提供基于配置的审批策略：
- approve: 自动批准
- prompt: 每次询问用户
- deny: 拒绝执行
"""

from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ApprovalDecision(Enum):
    """审批决策"""
    APPROVE = "approve"    # 自动批准
    PROMPT = "prompt"      # 询问用户
    DENY = "deny"          # 拒绝执行


class MCPToolApproval:
    """MCP 工具调用审批"""

    def __init__(self, config: Optional[dict] = None):
        """初始化审批系统

        Args:
            config: 审批配置，格式为:
                {
                    "github": {
                        "create_issue": "approve",
                        "delete_repo": "prompt"
                    },
                    "filesystem": {
                        "read_file": "approve",
                        "write_file": "prompt"
                    }
                }
        """
        self._approvals: dict[str, dict[str, str]] = config or {}

    def load_from_config(self, mcp_config: dict):
        """从 MCP 配置加载审批规则

        Args:
            mcp_config: MCP 配置字典
        """
        approvals = mcp_config.get("approvals", {})
        self._approvals = {
            server: tools if isinstance(tools, dict) else {}
            for server, tools in approvals.items()
        }
        logger.info(f"Loaded approval rules: {self._approvals}")

    def get_default_policy(self, server: str, tool: str) -> ApprovalDecision:
        """获取默认审批策略

        默认策略：
        - 只读操作 (read 开头): 自动批准
        - 危险操作 (delete, remove, destroy): 询问
        - 其他: 自动批准

        Args:
            server: MCP 服务器名称
            tool: 工具名称

        Returns:
            审批决策
        """
        tool_lower = tool.lower()

        # 危险操作默认询问
        dangerous_prefixes = ["delete", "remove", "destroy", "drop", "truncate"]
        if any(tool_lower.startswith(prefix) for prefix in dangerous_prefixes):
            return ApprovalDecision.PROMPT

        # 写操作默认询问
        write_verbs = ["create", "write", "update", "edit", "modify"]
        if any(tool_lower.startswith(verb) for verb in write_verbs):
            return ApprovalDecision.PROMPT

        # 读操作默认批准
        if tool_lower.startswith("read") or tool_lower.startswith("list"):
            return ApprovalDecision.APPROVE

        # 默认为询问
        return ApprovalDecision.APPROVE

    async def check(self, server: str, tool: str, arguments: dict = None) -> ApprovalDecision:
        """检查工具调用是否需要审批

        Args:
            server: MCP 服务器名称
            tool: 工具名称
            arguments: 工具参数（可选）

        Returns:
            审批决策
        """
        logger.debug(f"Approval: 检查工具 mcp__{server}__{tool}")

        # 检查是否有配置的审批规则
        server_approvals = self._approvals.get(server, {})
        if tool in server_approvals:
            policy = server_approvals[tool]
            decision = ApprovalDecision(policy)
            logger.info(f"Approval: 工具 mcp__{server}__{tool} 使用配置策略: {decision.value}")
            return decision

        # 使用默认策略
        default_decision = self.get_default_policy(server, tool)
        logger.info(f"Approval: 工具 mcp__{server}__{tool} 使用默认策略: {default_decision.value}")
        return default_decision

    def set_policy(self, server: str, tool: str, decision: ApprovalDecision):
        """设置审批策略

        Args:
            server: MCP 服务器名称
            tool: 工具名称
            decision: 审批决策
        """
        if server not in self._approvals:
            self._approvals[server] = {}
        self._approvals[server][tool] = decision.value

    def get_policy(self, server: str, tool: str) -> Optional[ApprovalDecision]:
        """获取指定工具的审批策略

        Args:
            server: MCP 服务器名称
            tool: 工具名称

        Returns:
            审批策略，如果未设置则返回 None
        """
        policy = self._approvals.get(server, {}).get(tool)
        if policy:
            return ApprovalDecision(policy)
        return None

    def get_all_policies(self) -> dict[str, dict[str, str]]:
        """获取所有审批策略

        Returns:
            审批策略字典
        """
        return self._approvals


__all__ = ["ApprovalDecision", "MCPToolApproval"]