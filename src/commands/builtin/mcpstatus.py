"""MCP Status command - Display MCP server connection status"""
from typing import AsyncIterator

from ..base import Command, CommandContext, CommandResult, CommandResultType


class McpStatusCommand(Command):
    """/mcpstatus - Show MCP server connection status"""

    name = "mcpstatus"
    description = "显示 MCP 服务器连接状态"
    aliases = ["mcp"]
    requires_context = False

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute the mcpstatus command"""
        if not context.cli:
            yield CommandResult(
                type=CommandResultType.ERROR,
                content="无法获取 MCP 配置"
            )
            return

        mcp_config = context.cli.config.get("mcp", {}).get("servers") or []
        mcp_client = context.cli.mcp_client

        if not mcp_config:
            yield CommandResult(
                type=CommandResultType.OUTPUT,
                content="未配置 MCP 服务器"
            )
            return

        yield CommandResult(
            type=CommandResultType.THINKING,
            content="正在连接 MCP 服务器..."
        )

        from src.mcp.client import MCPServerConfig

        for server in mcp_config:
            server_name = server.get("name", "unknown")
            server_type = server.get("type", "stdio")
            enabled = server.get("enabled", True)

            if not enabled:
                continue

            if mcp_client.is_connected(server_name):
                continue

            try:
                if server_type == "http":
                    config = MCPServerConfig(
                        name=server_name,
                        type="http",
                        url=server.get("url"),
                        headers=server.get("headers", {}),
                        enabled=True
                    )
                else:
                    config = MCPServerConfig(
                        name=server_name,
                        type="stdio",
                        command=server.get("command"),
                        enabled=True,
                        env=server.get("env", {})
                    )
                if await mcp_client.connect(config):
                    yield CommandResult(
                        type=CommandResultType.SUCCESS,
                        content=f"✓ {server_name} 连接成功"
                    )
                else:
                    yield CommandResult(
                        type=CommandResultType.ERROR,
                        content=f"✗ {server_name} 连接失败"
                    )
            except Exception as e:
                yield CommandResult(
                    type=CommandResultType.ERROR,
                    content=f"✗ {server_name} 连接异常: {e}"
                )

        lines = ["\nMCP 服务器状态："]
        for server in mcp_config:
            server_name = server.get("name", "unknown")
            server_type = server.get("type", "stdio")
            enabled = server.get("enabled", True)

            if not enabled:
                status = "disabled"
            elif mcp_client.is_connected(server_name):
                status = "connected"
            else:
                status = "disconnected"

            lines.append(f"  {server_name} ({server_type}) - {status}")

        yield CommandResult(
            type=CommandResultType.SUCCESS,
            content="\n".join(lines)
        )


mcpstatus_command = McpStatusCommand()
