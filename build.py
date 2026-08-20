#!/usr/bin/env python
"""Build script for Nexus PyInstaller package

@file build.py
@brief 使用 PyInstaller 构建单文件 exe
@usage python build.py
"""
import os
import sys
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).parent
VENV_PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
# uv is installed globally at D:\Software\uv\uv.exe
UV_EXE = Path("D:/Software/uv/uv.exe")


def install_pyinstaller():
    """Install PyInstaller using uv (for uv-managed venv)"""
    print("安装 PyInstaller...")

    # Try uv first (for uv-managed venv)
    if UV_EXE.exists():
        subprocess.run(
            [str(UV_EXE), "pip", "install", "pyinstaller"],
            check=True,
            cwd=PROJECT_DIR
        )
    else:
        # Fallback to pip if uv not available
        python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
        subprocess.run(
            [python, "-m", "pip", "install", "pyinstaller"],
            check=True,
            cwd=PROJECT_DIR
        )
    print("PyInstaller 安装完成")


def build():
    """Run PyInstaller build"""
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    spec_file = PROJECT_DIR / "Nexus.spec"

    print(f"使用 Python: {python}")
    print(f"Spec 文件: {spec_file}")

    # Ensure PyInstaller is installed
    install_pyinstaller()

    # Run PyInstaller with --clean
    print("开始构建...")
    result = subprocess.run(
        [python, "-m", "PyInstaller", str(spec_file), "--clean"],
        cwd=PROJECT_DIR
    )

    if result.returncode == 0:
        print("\n构建成功!")
        print(f"输出目录: {PROJECT_DIR / 'dist' / 'Nexus'}")
        print(f"可执行文件: {PROJECT_DIR / 'dist' / 'Nexus' / 'Nexus.exe'}")
    else:
        print("\n构建失败!")
        sys.exit(1)


if __name__ == "__main__":
    build()
