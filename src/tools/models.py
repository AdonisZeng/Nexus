from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FileReadArgs(BaseModel):
    """文件读取参数模型"""

    file_path: str = Field(..., description="要读取的文件路径")
    offset: int = Field(default=1, ge=1, description="起始行号，从1开始")
    limit: int = Field(default=2000, ge=1, le=10000, description="读取的最大行数")
    mode: str = Field(default="slice", description="读取模式: 'slice' 或 'indentation'")
    anchor_line: Optional[int] = Field(default=None, description="锚定行号，用于缩进模式")


class FileWriteArgs(BaseModel):
    """文件写入参数模型"""

    file_path: str = Field(..., description="要写入的文件路径")
    content: str = Field(..., description="要写入的文件内容")


class FilePatchArgs(BaseModel):
    """Patch 编辑参数模型"""

    patch: str = Field(..., description="Patch 格式的文本内容")


class ListDirArgs(BaseModel):
    """目录列表参数模型"""

    dir_path: str = Field(..., description="要列出的目录路径")
    offset: int = Field(default=1, ge=1, description="起始条目索引，从1开始")
    limit: int = Field(default=25, ge=1, description="返回的最大条目数")
    depth: int = Field(default=2, ge=1, le=5, description="递归深度")


class ShellArgs(BaseModel):
    """Shell 执行参数模型"""

    command: str = Field(..., description="要执行的 shell 命令")
    cwd: Optional[str] = Field(default=None, description="工作目录路径")
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间（秒）")


class SearchArgs(BaseModel):
    """文件搜索参数模型"""

    pattern: str = Field(..., description="搜索的正则表达式模式")
    path: str = Field(default=".", description="搜索的起始路径")
    include: Optional[str] = Field(default=None, description="包含的文件模式（glob格式）")
    limit: int = Field(default=100, ge=1, le=2000, description="返回的最大结果数")


class CodeExecArgs(BaseModel):
    """代码执行参数模型"""

    code: str = Field(..., description="要执行的 Python 代码")
    timeout: int = Field(default=30, ge=1, le=300, description="超时时间（秒）")
