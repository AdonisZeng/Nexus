"""MCP Tool Adapter - 将 MCP 工具适配为 Tool 基类

将 MCP 服务器暴露的工具适配为统一的 Tool 接口，
使其可以注册到 ToolRegistry 并通过 ToolOrchestrator 执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from src.tools.registry import Tool
from src.permissions.registry import HIGH_RISK_PREFIXES, WRITE_PREFIXES, READ_PREFIXES

if TYPE_CHECKING:
    from src.mcp.router import MCPToolRouter


@dataclass
class MCPToolMetadata:
    """MCP 工具元数据"""
    server_name: str          # MCP 服务器名称
    original_name: str        # 原始工具名 (不含前缀)
    qualified_name: str       # 完整名称: mcp__{server}__{tool}
    description: str
    input_schema: dict


class MCPToolAdapter(Tool):
    """MCP 工具适配器

    将 MCP 服务器暴露的工具适配为统一的 Tool 接口，
    使其可以注册到 ToolRegistry 并通过 ToolOrchestrator 执行。
    """

    def __init__(
        self,
        metadata: MCPToolMetadata,
        mcp_router: "MCPToolRouter",
    ):
        self._metadata = metadata
        self._router = mcp_router

    @property
    def name(self) -> str:
        """工具名称: mcp__{server}__{tool}"""
        return self._metadata.qualified_name

    @property
    def description(self) -> str:
        return self._metadata.description

    @property
    def is_mutating(self) -> bool:
        """基于工具名前缀推断是否修改状态"""
        lowered = self._metadata.original_name.lower()
        if lowered.startswith(self.HIGH_RISK_PREFIXES):
            return True
        if lowered.startswith(self.WRITE_PREFIXES):
            return True
        return False

    @property
    def requires_approval(self) -> bool:
        """高危操作需要审批"""
        return self.is_mutating

    @property
    def is_concurrent_safe(self) -> bool:
        """MCP 工具默认不是并发安全的（因为不知道服务器端实现）"""
        return False

    @property
    def concurrency_category(self) -> str:
        """基于 is_mutating 返回 'read' 或 'write'"""
        return "write" if self.is_mutating else "read"

    def get_schema(self) -> dict:
        """返回工具 schema (用于 LLM)"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._metadata.input_schema,
        }

    async def execute(self, **kwargs) -> Any:
        """通过 MCPToolRouter 执行工具"""
        return await self._router.call_tool(
            server=self._metadata.server_name,
            tool=self._metadata.original_name,
            args=kwargs,
        )

    async def before_execute(self, **kwargs) -> Optional[str]:
        """执行前 Hook - 可返回错误消息以中止执行"""
        return None

    async def after_execute(self, result: Any, **kwargs):
        """执行后清理"""
        pass


class MCPToolAdapterFactory:
    """MCPToolAdapter 工厂类"""

    @staticmethod
    def create_from_mcp_schema(
        server_name: str,
        tool_schema: dict,
        mcp_router: "MCPToolRouter",
    ) -> MCPToolAdapter:
        """从 MCP 服务器返回的 schema 创建适配器

        Args:
            server_name: MCP 服务器名称
            tool_schema: MCP 服务器返回的工具 schema (含 name, description, inputSchema)
            mcp_router: MCPToolRouter 实例

        Returns:
            MCPToolAdapter 实例
        """
        original_name = tool_schema["name"]
        qualified_name = f"mcp__{server_name}__{original_name}"

        # 处理 inputSchema 可能是 dict 或直接是 schema 的情况
        input_schema = tool_schema.get("inputSchema", tool_schema.get("input_schema", {}))
        if isinstance(input_schema, str):
            # 如果是 JSON 字符串，尝试解析
            import json
            try:
                input_schema = json.loads(input_schema)
            except (json.JSONDecodeError, TypeError):
                input_schema = {"type": "object", "properties": {}}

        metadata = MCPToolMetadata(
            server_name=server_name,
            original_name=original_name,
            qualified_name=qualified_name,
            description=tool_schema.get("description", ""),
            input_schema=input_schema,
        )

        return MCPToolAdapter(metadata=metadata, mcp_router=mcp_router)

    @staticmethod
    def create_adapter_set(
        server_name: str,
        tool_schemas: list[dict],
        mcp_router: "MCPToolRouter",
    ) -> dict[str, MCPToolAdapter]:
        """批量创建适配器，返回 {qualified_name: adapter} 字典

        Args:
            server_name: MCP 服务器名称
            tool_schemas: MCP 服务器返回的工具列表
            mcp_router: MCPToolRouter 实例

        Returns:
            工具名到适配器的字典
        """
        adapters = {}
        for schema in tool_schemas:
            adapter = MCPToolAdapterFactory.create_from_mcp_schema(
                server_name, schema, mcp_router
            )
            adapters[adapter.name] = adapter
        return adapters
