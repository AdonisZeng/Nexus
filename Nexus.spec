# -*- mode: python ; coding: utf-8 -*-
"""Nexus PyInstaller spec file

@file Nexus.spec
@brief PyInstaller 单文件打包配置
"""
import os
import sys
from pathlib import Path

block_cipher = None

# Project root directory - use NEXUS_ROOT env var or default location
project_root = Path(os.environ.get('NEXUS_ROOT', r'D:\Development\Python\Nexus'))

a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # 包含 config.yaml.template 到打包目录
        (str(project_root / 'config.yaml.template'), '.'),
    ],
    hiddenimports=[
        # Core
        'pkg_resources',
        'yaml',
        '_yaml',
        # Model clients
        'anthropic',
        'anthropic.messages',
        'anthropic.types',
        'openai',
        'openai.resources',
        'httpx',
        'httpx._utils',
        # CLI libraries
        'prompt_toolkit',
        'prompt_toolkit.application',
        'prompt_toolkit.buffer',
        'prompt_toolkit.completion',
        'prompt_toolkit.key_binding',
        'prompt_toolkit.layout',
        'prompt_toolkit.output',
        'prompt_toolkit.styles',
        'prompt_toolkit.validation',
        'rich',
        'rich.console',
        'rich.table',
        'rich.progress',
        'rich.panel',
        'rich.syntax',
        'rich.text',
        'rich.markdown',
        'typer',
        'typer.main',
        'typer.models',
        'click',
        # Src modules (ensure they are included)
        'src',
        'src.config',
        'src.agent',
        'src.adapters',
        'src.adapters.anthropic',
        'src.adapters.openai',
        'src.adapters.ollama',
        'src.adapters.lmstudio',
        'src.adapters.minimax',
        'src.adapters.xai',
        'src.adapters.custom',
        'src.cli',
        'src.cli.main',
        'src.cli.history',
        'src.context',
        'src.context.core',
        'src.tasks',
        'src.tasks.manager',
        'src.team',
        'src.team.database',
        'src.team.storage',
        'src.team.task_board',
        'src.team.worktree_manager',
        'src.team.agent',
        'src.commands',
        'src.commands.registry',
        'src.commands.builtin',
        'src.tools',
        'src.tools.registry',
        'src.utils',
        'src.utils.logger',
        'src.skills',
        'src.skills.loader',
        'src.skills.scope',
        'src.mcp',
        'src.todo',
        # Office tools (optional)
        'docx',
        'docx.table',
        'docx.text.paragraph',
        'openpyxl',
        'openpyxl.workbook',
        'pptx',
        'pptx.presentation',
        'pptx.util',
        'pypdf',
        'pypdf._reader',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'venv',
        '.venv',
        'tests',
        'docs',
        '.git',
        '.github',
        'logs',
        '.nexus',
        '__pycache__',
        '*.pyc',
        '*.pyo',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Nexus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Nexus',
)
