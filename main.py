#!/usr/bin/env python
"""Nexus - Personal AI Agent

@file main.py
@brief Nexus 应用程序入口点
@details 支持 CLI 界面模式

Usage:
    python main.py                    # CLI mode
    python main.py "task"             # CLI single task
    python main.py --config custom.yaml
    python main.py --model ollama
"""

import asyncio
import argparse
import sys
from pathlib import Path


def get_exe_dir() -> Path:
    """Get the directory where the exe/script is located

    @return exe 或脚本所在目录路径
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def get_default_config_path() -> str:
    """Get default config path based on running mode

    @return 配置文件的默认路径
    """
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


def main():
    """@brief 主入口函数

    @details 解析命令行参数并根据参数启动相应的界面模式
    """
    # 初始化日志系统
    logger = setup_logger()
    logger.info("Nexus 应用启动")
    logger.info(f"Python 版本: {sys.version}")
    logger.info(f"工作目录: {Path.cwd()}")

    parser = argparse.ArgumentParser(description="Nexus - Personal AI Agent")
    parser.add_argument("task", nargs="?", help="要执行的任务")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径")
    parser.add_argument("--model", choices=["anthropic", "openai", "ollama", "lmstudio", "custom"], help="使用的模型")
    args = parser.parse_args()

    logger.debug(f"命令行参数: {vars(args)}")

    config_path = args.config if args.config else get_default_config_path()
    config = load_config(config_path)
    logger.info(f"配置加载完成: {config_path}")

    if args.model:
        config.setdefault("models", {})["default"] = args.model
        logger.info(f"模型设置为: {args.model}")

    logger.info("启动模式: CLI")
    cli = NexusCLI(config)
    try:
        asyncio.run(cli.initialize())
        if args.task:
            logger.info(f"执行单次任务: {args.task}")
            asyncio.run(cli.run_single(args.task))
        else:
            logger.info("启动交互式 CLI 会话")
            asyncio.run(cli.chat())
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