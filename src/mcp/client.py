"""MCP Client - Model Context Protocol

支持两种连接模式:
1. stdio: 本地命令模式 (默认)
2. http: HTTP/SSE 远程服务器模式

配置格式示例:
    # stdio 模式
    - name: filesystem
      type: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path"]
      enabled: true
      env: {}

    # http 模式
    - name: github
      type: http
      url: https://api.githubcopilot.com/mcp/
      headers:
        Authorization: Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}
      enabled: true
"""
import asyncio
import json
import logging
import os
import re
from typing import Any, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("Nexus")

import httpx


class MCPConnectionType(Enum):
    """MCP 连接类型"""
    STDIO = "stdio"
    HTTP = "http"


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server

    支持两种模式:
    - stdio: 需要 command 字段
    - http: 需要 url 字段
    """
    name: str
    type: str = "stdio"  # "stdio" 或 "http"
    enabled: bool = True

    # stdio 模式专用
    command: Optional[list[str]] = None
    env: dict = field(default_factory=dict)

    # http 模式专用
    url: Optional[str] = None
    headers: dict = field(default_factory=dict)

    def __post_init__(self):
        """验证配置有效性"""
        if self.type == MCPConnectionType.STDIO.value:
            if not self.command:
                raise ValueError(f"MCP server '{self.name}': stdio 模式需要提供 command 字段")
        elif self.type == MCPConnectionType.HTTP.value:
            if not self.url:
                raise ValueError(f"MCP server '{self.name}': http 模式需要提供 url 字段")
        else:
            raise ValueError(f"MCP server '{self.name}': 不支持的类型 '{self.type}'，只能是 'stdio' 或 'http'")


class MCPHTTPClient:
    """HTTP 模式的 MCP 客户端

    通过 HTTP/SSE 与远程 MCP 服务器通信
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.client: Optional[httpx.AsyncClient] = None
        self._request_id = 0
        self._tools: list[dict] = []

    async def connect(self) -> bool:
        """连接到 HTTP MCP 服务器"""
        try:
            # 处理 headers 中的环境变量
            headers = self._process_headers(self.config.headers)

            # 创建 HTTP 客户端
            self.client = httpx.AsyncClient(
                base_url=self.config.url.rstrip("/"),
                headers=headers,
                timeout=30.0
            )

            # 发送初始化请求
            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "nexus",
                    "version": "0.1.0"
                }
            })

            # 获取工具列表
            await self.list_tools()

            return True

        except Exception as e:
            logger.warning(f"Failed to connect to HTTP MCP server {self.config.name}: {e}")
            return False

    async def disconnect(self):
        """断开连接"""
        if self.client:
            await self.client.aclose()
            self.client = None

    def _process_headers(self, headers: dict) -> dict:
        """处理 headers 中的环境变量占位符"""
        processed = {}
        for key, value in headers.items():
            if isinstance(value, str):
                # 使用正则表达式替换所有 ${VAR} 格式的环境变量
                pattern = r'\$\{([^}]+)\}'
                processed[key] = re.sub(pattern, lambda m: os.environ.get(m.group(1), m.group(0)), value)
            else:
                processed[key] = value
        return processed

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求（支持 SSE 响应格式）"""
        if not self.client:
            raise RuntimeError("HTTP client not connected")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params
        }

        response = await self.client.post(
            "/",
            json=request
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type or "event-stream" in content_type:
            return self._parse_sse_response(response.text, request["id"])
        else:
            data = response.json()
            if "error" in data:
                raise Exception(f"MCP error: {data['error']}")
            return data.get("result", {})

    def _parse_sse_response(self, text: str, request_id: int) -> dict:
        """解析 SSE 格式响应"""
        import json
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if data_str:
                    try:
                        data = json.loads(data_str)
                        if data.get("id") == request_id:
                            if "error" in data:
                                raise Exception(f"MCP error: {data['error']}")
                            return data.get("result", {})
                    except json.JSONDecodeError:
                        continue
        return {}

    async def list_tools(self) -> list[dict]:
        """获取可用工具列表"""
        result = await self._send_request("tools/list", {})
        tools = result.get("tools", [])
        self._tools = tools
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """调用工具"""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        return result

    def get_tools(self) -> list[dict]:
        """获取缓存的工具列表"""
        return self._tools


class MCPStdioClient:
    """STDIO 模式的 MCP 客户端

    通过子进程 stdin/stdout 与本地 MCP 服务器通信
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._tools: list[dict] = []

    async def connect(self) -> bool:
        """连接到 STDIO MCP 服务器"""
        try:
            # 创建环境变量
            env = os.environ.copy()
            env.update(self.config.env)

            # 启动 MCP 服务器进程
            self.process = await asyncio.create_subprocess_exec(
                *self.config.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            # 发送初始化请求
            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "nexus",
                    "version": "0.1.0"
                }
            })

            # 发送 initialized 通知
            await self._send_notification("initialized")

            # 获取工具列表
            await self.list_tools()

            return True

        except Exception as e:
            logger.warning(f"Failed to connect to STDIO MCP server {self.config.name}: {e}")
            return False

    async def disconnect(self):
        """断开连接"""
        if self.process:
            self.process.terminate()
            await self.process.wait()
            self.process = None

    async def _send_request(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求"""
        if not self.process:
            raise RuntimeError("Process not connected")

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params
        }

        self.process.stdin.write(json.dumps(request).encode() + b'\n')
        await self.process.stdin.drain()

        # 读取响应
        line = await self.process.stdout.readline()
        response = json.loads(line)

        if "error" in response:
            raise Exception(f"MCP error: {response['error']}")

        return response.get("result", {})

    async def _send_notification(self, method: str, params: dict = None):
        """发送 JSON-RPC 通知"""
        if not self.process:
            raise RuntimeError("Process not connected")

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }

        self.process.stdin.write(json.dumps(notification).encode() + b'\n')
        await self.process.stdin.drain()

    async def list_tools(self) -> list[dict]:
        """获取可用工具列表"""
        result = await self._send_request("tools/list", {})
        tools = result.get("tools", [])
        self._tools = tools
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """调用工具"""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        return result

    def get_tools(self) -> list[dict]:
        """获取缓存的工具列表"""
        return self._tools


def parse_qualified_tool_name(name: str) -> tuple[str, str]:
    """解析 mcp__{server}__{tool} 格式的工具名

    Args:
        name: 格式为 "mcp__{server}__{tool}" 的工具名

    Returns:
        (server_name, tool_name)

    Raises:
        ValueError: 格式不正确
    """
    prefix = "mcp__"
    if not name.startswith(prefix):
        raise ValueError(f"Invalid MCP tool name: {name}. Must start with '{prefix}'")

    parts = name[len(prefix):].split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid MCP tool name format: {name}. Expected 'mcp__{{server}}__{{tool}}'")

    return parts[0], parts[1]


class MCPClient:
    """统一的 MCP 客户端

    支持 stdio 和 http 两种模式，自动根据配置创建对应的客户端
    """

    def __init__(self):
        self.servers: dict[str, MCPStdioClient | MCPHTTPClient] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._startup_snapshots: dict[str, list[dict]] = {}  # 工具列表缓存
        self._cancel_tokens: dict[str, asyncio.Event] = {}  # 取消令牌

    async def connect(self, config: MCPServerConfig) -> bool:
        """连接到 MCP 服务器"""
        logger.info(f"MCP: 正在连接服务器 '{config.name}' (type={config.type})")

        if not config.enabled:
            logger.info(f"MCP: 服务器 '{config.name}' 已禁用，跳过连接")
            return False

        # 根据类型创建对应的客户端
        if config.type == MCPConnectionType.STDIO.value:
            client = MCPStdioClient(config)
            logger.debug(f"MCP: STDIO 模式连接服务器 '{config.name}', command={config.command}")
        elif config.type == MCPConnectionType.HTTP.value:
            client = MCPHTTPClient(config)
            logger.debug(f"MCP: HTTP 模式连接服务器 '{config.name}', url={config.url}")
        else:
            logger.warning(f"MCP: 不支持的服务器类型 '{config.type}'")
            return False

        # 尝试连接
        try:
            if await client.connect():
                self.servers[config.name] = client
                self._configs[config.name] = config
                # 缓存启动时的工具列表快照
                tool_count = len(client.get_tools())
                self._startup_snapshots[config.name] = client.get_tools()
                logger.info(f"MCP: 服务器 '{config.name}' 连接成功, 工具数量: {tool_count}")
                return True
            else:
                logger.warning(f"MCP: 服务器 '{config.name}' 连接失败")
                return False
        except Exception as e:
            logger.error(f"MCP: 服务器 '{config.name}' 连接异常: {e}")
            return False

    async def initialize_server(
        self,
        config: MCPServerConfig,
        startup_timeout: float = 10.0,
    ) -> bool:
        """异步初始化服务器，支持超时控制

        Args:
            config: MCP 服务器配置
            startup_timeout: 超时时间（秒）

        Returns:
            连接是否成功
        """
        import asyncio

        logger.info(f"MCP: 开始初始化服务器 '{config.name}', 超时时间: {startup_timeout}s")
        cancel_token = asyncio.Event()
        self._cancel_tokens[config.name] = cancel_token

        try:
            result = await asyncio.wait_for(
                self.connect(config),
                timeout=startup_timeout
            )
            if result:
                logger.info(f"MCP: 服务器 '{config.name}' 初始化成功")
            else:
                logger.warning(f"MCP: 服务器 '{config.name}' 初始化失败")
            return result
        except asyncio.TimeoutError:
            logger.warning(f"MCP: 服务器 '{config.name}' 初始化超时 ({startup_timeout}s)")
            await self.disconnect(config.name)
            return False
        except Exception as e:
            logger.error(f"MCP: 服务器 '{config.name}' 初始化异常: {e}")
            await self.disconnect(config.name)
            return False

    def get_startup_snapshot(self, server: str) -> list[dict]:
        """获取服务器启动时的工具列表快照

        Args:
            server: 服务器名称

        Returns:
            工具列表快照，如果不存在则返回空列表
        """
        return self._startup_snapshots.get(server, [])

    def create_cancel_token(self, server: str) -> asyncio.Event:
        """为服务器创建取消令牌

        Args:
            server: 服务器名称

        Returns:
            取消令牌（asyncio.Event）
        """
        token = asyncio.Event()
        self._cancel_tokens[server] = token
        return token

    def cancel_operation(self, server: str):
        """取消服务器的操作

        Args:
            server: 服务器名称
        """
        if server in self._cancel_tokens:
            self._cancel_tokens[server].set()

    def is_cancelled(self, server: str) -> bool:
        """检查操作是否被取消

        Args:
            server: 服务器名称

        Returns:
            是否已取消
        """
        return self._cancel_tokens.get(server, asyncio.Event()).is_set()

    async def disconnect(self, name: str):
        """断开指定服务器"""
        logger.info(f"MCP: 正在断开服务器 '{name}'")
        if name in self.servers:
            await self.servers[name].disconnect()
            del self.servers[name]
            # 清理缓存
            if name in self._startup_snapshots:
                del self._startup_snapshots[name]
            if name in self._cancel_tokens:
                del self._cancel_tokens[name]
            if name in self._configs:
                del self._configs[name]
            logger.info(f"MCP: 服务器 '{name}' 已断开")

    async def disconnect_all(self):
        """断开所有服务器"""
        servers = list(self.servers.keys())
        logger.info(f"MCP: 正在断开所有服务器: {servers}")
        for name in servers:
            await self.disconnect(name)
        logger.info(f"MCP: 所有服务器已断开")

    async def list_tools(self, server: str) -> list[dict]:
        """获取服务器工具列表"""
        if server not in self.servers:
            logger.warning(f"MCP: 尝试列出未连接服务器 '{server}' 的工具")
            return []
        tools = await self.servers[server].list_tools()
        logger.debug(f"MCP: 服务器 '{server}' 工具列表: {len(tools)} 个")
        return tools

    async def call_tool(self, server: str, tool_name: str, arguments: dict) -> Any:
        """调用工具"""
        if server not in self.servers:
            logger.error(f"MCP: 尝试调用未连接服务器 '{server}' 的工具 '{tool_name}'")
            raise ValueError(f"Server not connected: {server}")

        logger.info(f"MCP: 调用工具 mcp__{server}__{tool_name}, 参数: {arguments}")
        try:
            result = await self.servers[server].call_tool(tool_name, arguments)
            logger.debug(f"MCP: 工具 mcp__{server}__{tool_name} 调用成功")
            return result
        except Exception as e:
            logger.error(f"MCP: 工具 mcp__{server}__{tool_name} 调用失败: {e}")
            raise

    def get_tools_schema(self, server: str) -> list[dict]:
        """获取工具 Schema

        使用 mcp__{server}__{tool} 格式避免命名冲突
        """
        if server not in self.servers:
            return []

        tools = self.servers[server].get_tools()
        return [
            {
                "name": f"mcp__{server}__{tool['name']}",
                "description": tool.get("description", ""),
                "input_schema": tool.get("inputSchema", {})
            }
            for tool in tools
        ]

    def list_servers(self) -> list[str]:
        """列出已连接的服务器"""
        return list(self.servers.keys())

    def is_connected(self, server: str) -> bool:
        """检查服务器是否已连接"""
        return server in self.servers

    def get_server_config(self, server: str) -> Optional[MCPServerConfig]:
        """获取服务器配置"""
        return self._configs.get(server)
