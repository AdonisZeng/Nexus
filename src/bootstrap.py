"""Bootstrap module for PyInstaller single-file exe

@file bootstrap.py
@brief 处理 exe 运行时环境的初始化
@details 检测是否在打包环境中运行，初始化 logs 目录和配置文件
"""
import sys
import os
import shutil
from pathlib import Path


def get_exe_dir() -> Path:
    """Get the directory where the exe is located

    @return exe 所在目录路径
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def bootstrap():
    """Initialize runtime environment for exe

    执行以下初始化:
    1. 确保 logs/ 目录在 exe 所在目录创建
    2. 如果 config.yaml 不存在，从模板复制
    3. 确保用户 ~/.nexus 目录存在
    """
    exe_dir = get_exe_dir()

    # 1. Ensure logs directory exists in exe directory
    logs_dir = exe_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 2. Copy config.yaml from template if not exists
    config_path = exe_dir / "config.yaml"
    if not config_path.exists():
        if getattr(sys, 'frozen', False):
            # In PyInstaller bundle, template is in _MEIPASS
            template_path = Path(sys._MEIPASS) / "config.yaml.template"
        else:
            template_path = exe_dir / "config.yaml.template"

        if template_path.exists():
            shutil.copy(template_path, config_path)

    # 3. Ensure user ~/.nexus directory exists
    user_nexus = Path.home() / ".nexus"
    user_nexus.mkdir(parents=True, exist_ok=True)
