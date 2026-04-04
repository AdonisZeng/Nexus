"""
@file logger.py
@brief Nexus 日志模块
@details 提供统一的日志记录功能，支持控制台和文件双重输出
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_logger: Optional[logging.Logger] = None
_log_file_path: Optional[Path] = None


def setup_logger(
    log_dir: str = None,
    level: int = logging.INFO,
    console: bool = False,
) -> logging.Logger:
    """
    @brief 初始化日志系统
    @param log_dir 日志目录路径（默认在 exe 所在目录或当前目录的 logs）
    @param level 文件日志级别
    @param console 是否启用控制台输出（默认关闭，仅输出到文件）
    @return 配置好的 Logger 实例
    """
    global _logger, _log_file_path

    if _logger is not None:
        return _logger

    # 禁用所有现有的 handlers（包括 root logger 的控制台输出）
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(logging.CRITICAL)  # 禁用 root logger 的默认输出

    # Determine log directory - exe aware
    if log_dir is None:
        if getattr(sys, 'frozen', False):
            log_dir = Path(sys.executable).parent / "logs"
        else:
            log_dir = Path("logs")
    else:
        log_dir = Path(log_dir)

    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _log_file_path = log_dir / f"{timestamp}.txt"

    _logger = logging.getLogger("Nexus")
    _logger.setLevel(level)

    _logger.handlers.clear()

    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(
        _log_file_path,
        encoding="utf-8",
        mode="w"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(file_formatter)
    _logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(file_formatter)
        _logger.addHandler(console_handler)

    return _logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    @brief 获取 Logger 实例
    @param name 模块名称（可选）
    @return Logger 实例
    """
    global _logger

    if _logger is None:
        setup_logger()

    if name:
        return _logger.getChild(name)

    return _logger


def get_log_file_path() -> Optional[Path]:
    """
    @brief 获取当前日志文件路径
    @return 日志文件路径
    """
    return _log_file_path


class LogContext:
    """
    @class LogContext
    @brief 日志上下文管理器，用于记录函数执行的进入和退出
    """

    def __init__(self, logger: logging.Logger, action: str, **kwargs):
        """
        @brief 构造函数
        @param logger Logger 实例
        @param action 操作名称
        @param kwargs 额外的上下文信息
        """
        self.logger = logger
        self.action = action
        self.context = kwargs
        self.start_time: Optional[datetime] = None

    def __enter__(self):
        """@brief 进入上下文"""
        self.start_time = datetime.now()
        context_str = " | ".join(f"{k}={v}" for k, v in self.context.items())
        self.logger.debug(f"[{self.action}] 开始 | {context_str}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """@brief 退出上下文"""
        elapsed = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0

        if exc_type is not None:
            self.logger.error(
                f"[{self.action}] 失败 | 耗时: {elapsed:.2f}s | 错误: {exc_val}",
                exc_info=True
            )
        else:
            self.logger.debug(f"[{self.action}] 完成 | 耗时: {elapsed:.2f}s")

        return False


def log_function_call(func):
    """
    @brief 函数调用日志装饰器
    @param func 被装饰的函数
    @return 装饰后的函数
    """
    import functools

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        logger.debug(f"调用函数: {func.__name__} | args={args[:2]}... | kwargs={list(kwargs.keys())}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"函数返回: {func.__name__}")
            return result
        except Exception as e:
            logger.error(f"函数异常: {func.__name__} | 错误: {e}", exc_info=True)
            raise

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        logger.debug(f"调用异步函数: {func.__name__} | args={args[:2]}... | kwargs={list(kwargs.keys())}")
        try:
            result = await func(*args, **kwargs)
            logger.debug(f"异步函数返回: {func.__name__}")
            return result
        except Exception as e:
            logger.error(f"异步函数异常: {func.__name__} | 错误: {e}", exc_info=True)
            raise

    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
