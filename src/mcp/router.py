"""MCP Tool Router - 工具路由与连接管理

管理多个 MCP 服务器连接、工具路由、自动重连。
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from src.mcp.adapter import MCPToolAdapter, MCPToolAdapterFactory
from src.mcp.client import MCPClient, MCPServerConfig

if TYPE_CHECKING:
    from src.tools.registry import Tool, ToolRegistry

logger = logging.getLogger("Nexus")


class ServerHealthStatus(Enum):
    """服务器健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


class ServerHealth:
    """服务器健康信息"""

    def __init__(
        self,
        status: ServerHealthStatus = ServerHealthStatus.DISCONNECTED,
        last_check: float = 0.0,
        consecutive_failures: int = 0,
        last_error: Optional[str] = None,
    ):
        self.status = status
        self.last_check = last_check
        self.consecutive_failures = consecutive_failures
        self.last_error = last_error


class RouterConfig:
    """Router 配置"""

    def __init__(
        self,
        health_check_interval: float = 30.0,    # 健康检查间隔 (秒)
        max_consecutive_failures: int = 3,       # 最大连续失败次数
        reconnect_delay: float = 5.0,            # 重连延迟 (秒)
        max_reconnect_attempts: int = 3,         # 最大重连次数
    ):
        self.health_check_interval = health_check_interval
        self.max_consecutive_failures = max_consecutive_failures
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts


class MCPToolRouter:
    """MCP 工具路由器

    职责:
    1. 管理多个 MCP 服务器连接
    2. 将工具调用路由到正确的服务器
    3. 自动重连断开的服务器
    4. 提供工具注册接口 (用于 ToolRegistry)
    """

    def __init__(
        self,
        mcp_client: Optional[MCPClient] = None,
        config: Optional[RouterConfig] = None,
    ):
        """初始化路由器

        Args:
            mcp_client: 可选的 MCPClient 实例 (复用现有连接)
            config: Router 配置
        """
        self._mcp_client = mcp_client or MCPClient()
        self._config = config or RouterConfig()

        # 服务器健康状态
        self._health: dict[str, ServerHealth] = {}

        # 已注册的适配器
        self._adapters: dict[str, MCPToolAdapter] = {}

        # 重连任务
        self._reconnect_tasks: dict[str, asyncio.Task] = {}

        # 健康检查任务
        self._health_check_task: Optional[asyncio.Task] = None
        self._shutdown = False

    # ──────────────────────────────────────────────
    # 服务器连接管理
    # ──────────────────────────────────────────────

    async def connect_server(self, config: MCPServerConfig) -> bool:
        """连接单个 MCP 服务器

        Args:
            config: 服务器配置

        Returns:
            连接是否成功
        """
        server_name = config.name

        if self._mcp_client.is_connected(server_name):
            logger.debug(f"[MCPToolRouter] 服务器 '{server_name}' 已连接")
            return True

        success = await self._mcp_client.connect(config)

        if success:
            self._health[server_name] = ServerHealth(
                status=ServerHealthStatus.HEALTHY,
            )
            await self._refresh_tools(server_name)
            logger.info(f"[MCPToolRouter] 服务器 '{server_name}' 连接成功")
        else:
            self._health[server_name] = ServerHealth(
                status=ServerHealthStatus.DISCONNECTED,
                consecutive_failures=1,
            )
            logger.warning(f"[MCPToolRouter] 服务器 '{server_name}' 连接失败")

        return success

    async def connect_servers(self, configs: list[MCPServerConfig]) -> dict[str, bool]:
        """批量连接服务器

        Args:
            configs: 服务器配置列表

        Returns:
            {server_name: success} 字典
        """
        if not configs:
            return {}

        # 并行连接所有服务器
        tasks = [self.connect_server(config) for config in configs]
        results_list = await asyncio.gather(*tasks)
        return {config.name: success for config, success in zip(configs, results_list)}

    async def disconnect_server(self, server_name: str) -> None:
        """断开服务器连接

        Args:
            server_name: 服务器名称
        """
        if server_name in self._reconnect_tasks:
            self._reconnect_tasks[server_name].cancel()
            try:
                await self._reconnect_tasks[server_name]
            except asyncio.CancelledError:
                pass
            del self._reconnect_tasks[server_name]

        await self._mcp_client.disconnect(server_name)

        # 移除该服务器的适配器
        to_remove = [
            name for name, adapter in self._adapters.items()
            if adapter._metadata.server_name == server_name
        ]
        for name in to_remove:
            del self._adapters[name]

        if server_name in self._health:
            self._health[server_name] = ServerHealth(status=ServerHealthStatus.DISCONNECTED)
        logger.info(f"[MCPToolRouter] 服务器 '{server_name}' 已断开")

    async def disconnect_all(self) -> None:
        """断开所有服务器"""
        self._shutdown = True

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        for task in list(self._reconnect_tasks.values()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reconnect_tasks.clear()

        await self._mcp_client.disconnect_all()
        self._adapters.clear()
        self._health.clear()
        logger.info("[MCPToolRouter] 所有服务器已断开")

    # ──────────────────────────────────────────────
    # 工具注册
    # ──────────────────────────────────────────────

    def register_tools_to_registry(self, registry: "ToolRegistry") -> int:
        """将 MCP 工具注册到 ToolRegistry

        Args:
            registry: ToolRegistry 实例

        Returns:
            注册的工具数量
        """
        count = 0
        for name, adapter in self._adapters.items():
            if name not in registry.tools:
                registry.register(adapter)
                count += 1
            else:
                logger.debug(
                    f"[MCPToolRouter] 工具 '{name}' 已存在，跳过 MCP 注册"
                )
        logger.info(f"[MCPToolRouter] 已注册 {count} 个 MCP 工具到 ToolRegistry")
        return count

    def get_adapter(self, qualified_name: str) -> Optional[MCPToolAdapter]:
        """获取工具适配器

        Args:
            qualified_name: 完整工具名 (mcp__{server}__{tool})

        Returns:
            适配器实例或 None
        """
        return self._adapters.get(qualified_name)

    def get_all_adapters(self) -> list[MCPToolAdapter]:
        """获取所有已注册的适配器"""
        return list(self._adapters.values())

    def get_tools_schema(self) -> list[dict]:
        """获取所有工具的 schema (用于 LLM)"""
        return [adapter.get_schema() for adapter in self._adapters.values()]

    # ──────────────────────────────────────────────
    # 工具调用路由
    # ──────────────────────────────────────────────

    def is_mcp_tool(self, tool_name: str) -> bool:
        """检查是否是 MCP 工具

        Args:
            tool_name: 工具名称

        Returns:
            是否是 MCP 工具
        """
        return tool_name.startswith("mcp__")

    async def call_tool(
        self,
        server: str,
        tool: str,
        args: dict,
    ) -> Any:
        """调用 MCP 工具

        Args:
            server: 服务器名称
            tool: 工具名称 (不含前缀)
            args: 工具参数

        Returns:
            工具执行结果

        Raises:
            ValueError: 服务器未连接
        """
        if not self._mcp_client.is_connected(server):
            # 尝试重连
            health = self._health.get(server)
            if health and health.consecutive_failures < self._config.max_consecutive_failures:
                logger.info(f"[MCPToolRouter] 尝试重连服务器 '{server}'")
                config = self._mcp_client.get_server_config(server)
                if config:
                    await self.connect_server(config)

            if not self._mcp_client.is_connected(server):
                raise ValueError(f"Server not connected: {server}")

        try:
            result = await self._mcp_client.call_tool(server, tool, args)
            self._update_health(server, success=True)
            return result
        except Exception as e:
            self._update_health(server, success=False, error=str(e))
            raise

    async def call_tool_by_name(self, qualified_name: str, args: dict) -> Any:
        """通过完整名称调用工具

        Args:
            qualified_name: 完整工具名 (mcp__{server}__{tool})
            args: 工具参数

        Returns:
            工具执行结果
        """
        from src.mcp.client import parse_qualified_tool_name
        server, tool = parse_qualified_tool_name(qualified_name)
        return await self.call_tool(server, tool, args)

    # ──────────────────────────────────────────────
    # 健康检查与重连
    # ──────────────────────────────────────────────

    def _update_health(
        self,
        server: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """更新服务器健康状态"""
        health = self._health.get(server)
        if not health:
            health = ServerHealth()
            self._health[server] = health

        health.last_check = time.time()

        if success:
            health.consecutive_failures = 0
            health.last_error = None
            health.status = ServerHealthStatus.HEALTHY
        else:
            health.consecutive_failures += 1
            health.last_error = error

            if health.consecutive_failures >= self._config.max_consecutive_failures:
                health.status = ServerHealthStatus.DISCONNECTED
                self._schedule_reconnect(server)

    def _schedule_reconnect(self, server: str) -> None:
        """调度重连任务"""
        if server in self._reconnect_tasks:
            task = self._reconnect_tasks[server]
            if not task.done():
                return  # 已在重连中
            # 清理已完成的任务
            del self._reconnect_tasks[server]

        loop = asyncio.get_event_loop()
        task = loop.create_task(self._reconnect_loop(server))
        self._reconnect_tasks[server] = task
        logger.info(f"[MCPToolRouter] 调度重连任务 for '{server}'")

    async def _reconnect_loop(self, server: str) -> None:
        """重连循环"""
        config = self._mcp_client.get_server_config(server)
        if not config:
            logger.warning(f"[MCPToolRouter] 无法重连 '{server}': 无配置信息")
            return

        attempts = 0
        while attempts < self._config.max_reconnect_attempts and not self._shutdown:
            attempts += 1
            logger.info(
                f"[MCPToolRouter] 重连尝试 {attempts}/{self._config.max_reconnect_attempts} "
                f"for '{server}'"
            )

            self._health[server] = ServerHealth(
                status=ServerHealthStatus.RECONNECTING,
            )

            success = await self._mcp_client.connect(config)

            if success:
                self._health[server] = ServerHealth(
                    status=ServerHealthStatus.HEALTHY,
                )
                await self._refresh_tools(server)
                logger.info(f"[MCPToolRouter] 重连成功: '{server}'")
                return

            await asyncio.sleep(self._config.reconnect_delay)

        self._health[server] = ServerHealth(
            status=ServerHealthStatus.DISCONNECTED,
            last_error=f"重连失败: {attempts} 次尝试",
        )
        logger.error(f"[MCPToolRouter] 重连失败: '{server}'")

    async def _refresh_tools(self, server: str) -> None:
        """刷新服务器的工具列表"""
        tools = await self._mcp_client.list_tools(server)

        # 获取现有适配器 (保留已有实例，只更新)
        existing = {
            name: adapter for name, adapter in self._adapters.items()
            if adapter._metadata.server_name == server
        }
        new_names = set()

        for schema in tools:
            adapter = MCPToolAdapterFactory.create_from_mcp_schema(
                server, schema, self
            )
            new_names.add(adapter.name)
            self._adapters[adapter.name] = adapter

        # 移除已不存在的工具
        for name in list(existing.keys()):
            if name not in new_names:
                if name in self._adapters:
                    del self._adapters[name]

        logger.debug(
            f"[MCPToolRouter] 服务器 '{server}' 工具已刷新: "
            f"{len(tools)} 个工具"
        )

    async def start_health_check(self) -> None:
        """启动健康检查循环"""
        async def _health_check_loop():
            while not self._shutdown:
                await asyncio.sleep(self._config.health_check_interval)
                await self._check_all_servers()

        loop = asyncio.get_event_loop()
        self._health_check_task = loop.create_task(_health_check_loop())

    async def _check_all_servers(self) -> None:
        """检查所有服务器健康状态"""
        for server_name in list(self._health.keys()):
            health = self._health.get(server_name)
            if not health:
                continue

            if health.status == ServerHealthStatus.RECONNECTING:
                continue

            # 检查是否断开 (MCPClient 认为已连接但实际无响应)
            if self._mcp_client.is_connected(server_name):
                # 尝试 ping/list_tools 检查实际连接
                try:
                    await self._mcp_client.list_tools(server_name)
                    if health.status != ServerHealthStatus.HEALTHY:
                        health.status = ServerHealthStatus.HEALTHY
                        health.consecutive_failures = 0
                except Exception as e:
                    logger.warning(
                        f"[MCPToolRouter] 健康检查失败 '{server_name}': {e}"
                    )
                    self._update_health(server_name, success=False, error=str(e))

    def get_health_status(self) -> dict[str, ServerHealth]:
        """获取所有服务器健康状态"""
        return dict(self._health)

    @property
    def mcp_client(self) -> MCPClient:
        """获取 MCPClient 实例"""
        return self._mcp_client

    @property
    def config(self) -> RouterConfig:
        """获取 Router 配置"""
        return self._config
