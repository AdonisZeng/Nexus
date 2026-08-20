#!/usr/bin/env python
"""Nexus - Personal AI Agent

@file main.py
@brief Nexus 应用程序入口点
@details 支持 TUI 全屏界面

Usage:
    python main.py                    # TUI mode
    python main.py "task"             # TUI single task
    python main.py --config custom.yaml
    python main.py --model ollama
"""

import asyncio
import argparse
import sys
from pathlib import Path


def get_default_config_path() -> str:
    """Get default config path based on running mode

    @return 配置文件的默认路径
    """
    from src.bootstrap import get_exe_dir
    return str(get_exe_dir() / "config.yaml")


# Bootstrap for PyInstaller exe - MUST be before other imports
if getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent))
    from src.bootstrap import bootstrap
    bootstrap()

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.cli.main import NexusCLI
from src.utils import setup_logger, get_logger
from src.adapters import AdapterRegistry


def main():
    """@brief 主入口函数

    @details 解析命令行参数并启动 TUI 界面
    """
    # 初始化日志系统
    logger = setup_logger()
    logger.info("Nexus 应用启动")
    logger.info(f"Python 版本: {sys.version}")
    logger.info(f"工作目录: {Path.cwd()}")

    parser = argparse.ArgumentParser(description="Nexus - Personal AI Agent")
    parser.add_argument("task", nargs="?", help="要执行的任务")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument("--model", choices=AdapterRegistry.list_providers(), help="使用的模型")
    args = parser.parse_args()

    logger.debug(f"命令行参数: {vars(args)}")

    config_path = args.config if args.config else get_default_config_path()
    config = load_config(config_path)
    logger.info(f"配置加载完成: {config_path}")

    if args.model:
        config.setdefault("models", {})["default"] = args.model
        logger.info(f"模型设置为: {args.model}")

    cli = NexusCLI(config, config_path)

    async def run_cli() -> None:
        """初始化后启动 TUI 会话"""
        await cli.initialize()
        if args.task:
            logger.info(f"执行单次任务: {args.task}")
            await cli.run_single(args.task)
        else:
            logger.info("启动 TUI 会话")
            await cli.run_tui()

    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        logger.info("用户中断操作 (Ctrl+C)")
    finally:
        try:
            if cli.memory_manager and cli.messages:
                cli.memory_manager.save_session(
                    cli.session_id,
                    cli.messages,
                    cli.current_title
                )
                logger.debug("会话已保存")
        except Exception as e:
            logger.error(f"保存会话失败: {e}")
        try:
            asyncio.run(cli.close())
        except Exception as e:
            logger.error(f"关闭 CLI 失败: {e}")

    logger.info("Nexus 应用退出")


if __name__ == "__main__":
    import signal

    if sys.platform == "win32":
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        main()
    except KeyboardInterrupt:
        pass
