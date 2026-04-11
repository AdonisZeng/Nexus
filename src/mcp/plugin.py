"""Plugin Loader - 插件发现与加载

从两个来源扫描插件:
1. 用户插件目录: ~/.nexus/plugins/
2. 项目插件目录: .claude-plugin/ (在项目根目录)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("Nexus")


class PluginManifest:
    """插件清单"""

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        mcp_servers: Optional[dict] = None,
        config_file: Optional[Path] = None,
    ):
        self.name = name
        self.version = version
        self.description = description
        self.mcp_servers = mcp_servers or {}
        self.config_file = config_file


class DiscoveredPlugin:
    """发现的插件"""

    def __init__(
        self,
        manifest: PluginManifest,
        root_dir: Path,
        source: str,  # "user" 或 "project"
    ):
        self.manifest = manifest
        self.root_dir = root_dir
        self.source = source


class PluginLoader:
    """插件加载器

    从两个来源扫描插件:
    1. 用户插件目录: ~/.nexus/plugins/
    2. 项目插件目录: .claude-plugin/ (在项目根目录)

    插件目录结构:
    ~/.nexus/plugins/<plugin_name>/
        plugin.json          # 插件清单
        config.yaml          # 可选配置文件

    或:

    <project_root>/
        .claude-plugin/
            plugin.json       # 插件清单
    """

    def __init__(
        self,
        user_plugins_dir: Optional[Path] = None,
        project_plugin_dir: Optional[Path] = None,
    ):
        """初始化插件加载器

        Args:
            user_plugins_dir: 用户插件目录 (默认 ~/.nexus/plugins/)
            project_plugin_dir: 项目插件目录 (默认 <cwd>/.claude-plugin/)
        """
        self._user_plugins_dir = user_plugins_dir or self._get_default_user_dir()
        self._project_plugin_dir = project_plugin_dir
        self._discovered_plugins: dict[str, DiscoveredPlugin] = {}

    @staticmethod
    def _get_default_user_dir() -> Path:
        """获取默认用户插件目录"""
        home = Path.home()
        return home / ".nexus" / "plugins"

    def scan(self) -> list[str]:
        """扫描所有插件目录，发现插件

        Returns:
            发现的插件名称列表
        """
        self._discovered_plugins.clear()

        # 扫描用户插件目录
        if self._user_plugins_dir and self._user_plugins_dir.exists():
            user_plugins = self._scan_directory(
                self._user_plugins_dir, source="user"
            )
            self._discovered_plugins.update(user_plugins)

        # 扫描项目插件目录
        if self._project_plugin_dir and self._project_plugin_dir.exists():
            project_plugins = self._scan_directory(
                self._project_plugin_dir, source="project"
            )
            self._discovered_plugins.update(project_plugins)

        found = list(self._discovered_plugins.keys())
        logger.info(f"[PluginLoader] 发现 {len(found)} 个插件: {found}")
        return found

    def _scan_directory(self, directory: Path, source: str) -> dict[str, DiscoveredPlugin]:
        """扫描单个目录下的所有插件

        Args:
            directory: 要扫描的目录
            source: 插件来源 ("user" 或 "project")

        Returns:
            {plugin_name: DiscoveredPlugin} 字典
        """
        discovered = {}

        if not directory.exists():
            return discovered

        for item in directory.iterdir():
            if not item.is_dir():
                continue

            manifest_path = item / "plugin.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = self._load_manifest(manifest_path)
                plugin = DiscoveredPlugin(
                    manifest=manifest,
                    root_dir=item,
                    source=source,
                )
                discovered[manifest.name] = plugin
                logger.debug(f"[PluginLoader] 发现插件: {manifest.name} ({source})")
            except Exception as e:
                logger.warning(f"[PluginLoader] 无法加载插件 {item}: {e}")

        return discovered

    def _load_manifest(self, path: Path) -> PluginManifest:
        """加载插件清单

        Args:
            path: plugin.json 路径

        Returns:
            PluginManifest 实例
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return PluginManifest(
            name=data.get("name", path.parent.name),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            mcp_servers=data.get("mcpServers", {}),
            config_file=path,
        )

    def get_mcp_server_configs(self) -> dict[str, dict]:
        """获取所有 MCP 服务器配置

        从所有已发现的插件中提取 MCP 服务器配置。
        服务器名称格式: plugin_name__server_name

        Returns:
            {qualified_server_name: config} 字典
        """
        configs = {}
        for name, plugin in self._discovered_plugins.items():
            for server_name, server_config in plugin.manifest.mcp_servers.items():
                qualified_name = f"{name}__{server_name}"
                configs[qualified_name] = self._normalize_server_config(server_config)
        return configs

    def _normalize_server_config(self, config: dict) -> dict:
        """规范化服务器配置

        确保配置包含必要的字段。

        Args:
            config: 原始配置

        Returns:
            规范化后的配置
        """
        normalized = dict(config)

        # 确保有 type 字段
        if "type" not in normalized:
            normalized["type"] = "stdio"

        return normalized

    def get_plugin(self, name: str) -> Optional[DiscoveredPlugin]:
        """获取指定插件

        Args:
            name: 插件名称

        Returns:
            插件信息或 None
        """
        return self._discovered_plugins.get(name)

    def get_all_plugins(self) -> dict[str, DiscoveredPlugin]:
        """获取所有已发现的插件"""
        return dict(self._discovered_plugins)

    def load_config_for_plugin(self, plugin_name: str) -> dict:
        """加载插件配置文件 (config.yaml)

        Args:
            plugin_name: 插件名称

        Returns:
            配置字典
        """
        plugin = self._discovered_plugins.get(plugin_name)
        if not plugin:
            return {}

        config_path = plugin.root_dir / "config.yaml"
        if not config_path.exists():
            return {}

        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"[PluginLoader] 无法加载插件配置 {config_path}: {e}")
            return {}

    def get_plugin_by_server(self, qualified_server_name: str) -> Optional[str]:
        """根据服务器名称获取插件名称

        Args:
            qualified_server_name: 格式 plugin_name__server_name

        Returns:
            插件名称或 None
        """
        if "__" not in qualified_server_name:
            return None
        return qualified_server_name.split("__", 1)[0]
