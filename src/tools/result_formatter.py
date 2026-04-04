"""Tool result formatter - standardizes tool output for LLM consumption"""
import json
from typing import Any, Optional
from dataclasses import dataclass


@dataclass
class ExecutionMetadata:
    """执行元数据"""
    duration_seconds: float
    exit_code: int = 0
    truncated: bool = False


@dataclass
class TruncationConfig:
    """截断配置"""
    max_chars: int = 8000
    max_lines: int = 100
    min_keep_chars: int = 2000
    preserve_tail_threshold: float = 0.3  # 保留尾部的比例


class SmartToolResultFormatter:
    """智能工具结果格式化器

    使用头+尾策略，在截断时保留重要的尾部信息。
    """

    # 指示尾部重要内容的关键词
    IMPORTANT_TAIL_KEYWORDS = [
        'error', 'exception', 'failed', 'fatal',
        'traceback', 'panic', 'stack trace',
        'total', 'summary', 'result', 'complete',
        'finished', 'done', 'exit code',
    ]

    @classmethod
    def smart_truncate(
        cls,
        text: str,
        config: Optional[TruncationConfig] = None
    ) -> str:
        """智能截断文本

        如果尾部包含重要信息（错误、结果等），则使用头+尾策略；
        否则仅保留头部。

        @param text: 原始文本
        @param config: 截断配置
        @return: 截断后的文本
        """
        if config is None:
            config = TruncationConfig()

        if len(text) <= config.max_chars:
            return text

        # 检查尾部是否包含重要内容
        tail_sample = text[-2000:].lower()
        has_important_tail = any(
            keyword in tail_sample
            for keyword in cls.IMPORTANT_TAIL_KEYWORDS
        )

        # 检查是否是 JSON 内容（以 } 或 ] 结尾）
        is_json_like = text.strip().endswith(('}', ']'))

        if (has_important_tail or is_json_like) and len(text) > config.min_keep_chars * 2:
            return cls._head_tail_truncate(text, config)
        else:
            return cls._head_only_truncate(text, config)

    @classmethod
    def _head_tail_truncate(cls, text: str, config: TruncationConfig) -> str:
        """头+尾截断策略"""
        tail_budget = int(config.max_chars * config.preserve_tail_threshold)
        head_budget = config.max_chars - tail_budget - 100  # 100 for marker

        # 在换行边界处截断头部
        head_cut = cls._find_line_boundary(text, head_budget, from_start=True)

        # 在换行边界处截断尾部
        tail_start = len(text) - tail_budget
        tail_cut = cls._find_line_boundary(text, tail_start, from_start=False)

        head_part = text[:head_cut]
        tail_part = text[tail_cut:]

        omitted_count = tail_cut - head_cut

        return (
            f"{head_part}\n\n"
            f"... [中间省略 {omitted_count} 字符] ...\n\n"
            f"{tail_part}"
        )

    @classmethod
    def _head_only_truncate(cls, text: str, config: TruncationConfig) -> str:
        """仅头部截断策略"""
        cut = cls._find_line_boundary(text, config.max_chars, from_start=True)

        return f"{text[:cut]}\n... [已截断，共 {len(text)} 字符]"

    @classmethod
    def _find_line_boundary(
        cls,
        text: str,
        target: int,
        from_start: bool = True
    ) -> int:
        """在目标位置附近找到换行边界

        @param text: 原始文本
        @param target: 目标位置
        @param from_start: 是否从开头计算
        @return: 换行边界位置
        """
        if from_start:
            # 向前找最近的换行
            last_newline = text.rfind('\n', 0, target)
            if last_newline > target * 0.8:  # 如果换行在目标位置的 80% 之后
                return last_newline
            return target
        else:
            # 向后找最近的换行
            next_newline = text.find('\n', target)
            if next_newline != -1 and next_newline < target + target * 0.2:
                return next_newline + 1
            return target


class ToolResultFormatter:
    """工具结果格式化器

    将工具结果格式化为模型友好的 JSON 格式，包含：
    - 执行状态（exit_code）
    - 执行时长
    - 截断标志
    """

    # Output limits
    MAX_OUTPUT_LINES = 100
    MAX_OUTPUT_CHARS = 8000

    @staticmethod
    def format_shell_result(result: dict, meta: ExecutionMetadata) -> str:
        """格式化 Shell 执行结果

        Args:
            result: 原始结果 dict，应包含 'output' 和可选的 'exit_code'
            meta: 执行元数据

        Returns:
            JSON 格式字符串
        """
        output = result.get('output', '') if isinstance(result, dict) else str(result)

        # Truncate long output
        truncated_output = ToolResultFormatter._truncate_output(output)

        formatted = {
            "tool": "shell",
            "exit_code": result.get('exit_code', 0) if isinstance(result, dict) else 0,
            "duration_seconds": round(meta.duration_seconds, 1),
            "output": truncated_output
        }

        if len(output) > ToolResultFormatter.MAX_OUTPUT_CHARS:
            formatted["truncated"] = True
            formatted["total_lines"] = len(output.splitlines())

        return json.dumps(formatted, ensure_ascii=False, indent=2)

    @staticmethod
    def format_file_result(result: Any, meta: ExecutionMetadata, file_path: str = None) -> str:
        """格式化文件读取结果

        Args:
            result: 文件内容或 dict
            meta: 执行元数据
            file_path: 文件路径（可选）

        Returns:
            JSON 格式字符串
        """
        if isinstance(result, dict):
            content = result.get('content', '')
        else:
            content = str(result)

        lines = content.splitlines()

        formatted = {
            "tool": "file_read",
            "file_path": file_path,
            "total_lines": len(lines),
            "content": ToolResultFormatter._truncate_output(content)
        }

        if len(lines) > ToolResultFormatter.MAX_OUTPUT_LINES:
            formatted["truncated"] = True
            formatted["showing_lines"] = ToolResultFormatter.MAX_OUTPUT_LINES

        return json.dumps(formatted, ensure_ascii=False, indent=2)

    @staticmethod
    def format_search_result(result: Any, meta: ExecutionMetadata) -> str:
        """格式化搜索结果

        Args:
            result: 搜索结果（通常为 list 或 dict）
            meta: 执行元数据

        Returns:
            JSON 格式字符串
        """
        if isinstance(result, list):
            total_matches = len(result)
            limited_results = result[:50]
        elif isinstance(result, dict):
            total_matches = result.get('total', len(result.get('matches', [])))
            limited_results = result.get('matches', [])[:50]
        else:
            total_matches = 1
            limited_results = [str(result)]

        formatted = {
            "tool": "search",
            "total_matches": total_matches,
            "showing": len(limited_results),
            "matches": limited_results
        }

        if total_matches > 50:
            formatted["note"] = "Results truncated to 50 matches"

        return json.dumps(formatted, ensure_ascii=False, indent=2)

    @staticmethod
    def format_list_dir_result(result: Any, meta: ExecutionMetadata, path: str = None) -> str:
        """格式化目录列表结果

        Args:
            result: 目录内容
            meta: 执行元数据
            path: 目录路径

        Returns:
            JSON 格式字符串
        """
        if isinstance(result, dict):
            entries = result.get('entries', [])
        elif isinstance(result, list):
            entries = result
        else:
            entries = [str(result)]

        formatted = {
            "tool": "list_dir",
            "path": path,
            "total_entries": len(entries),
            "entries": entries[:100]  # Limit to 100 entries
        }

        if len(entries) > 100:
            formatted["truncated"] = True

        return json.dumps(formatted, ensure_ascii=False, indent=2)

    @staticmethod
    def format_generic_result(result: Any, meta: ExecutionMetadata, tool_name: str = None) -> str:
        """通用格式化

        Args:
            result: 任意结果
            meta: 执行元数据
            tool_name: 工具名称

        Returns:
            JSON 格式字符串
        """
        formatted = {
            "tool": tool_name or "unknown",
            "duration_seconds": round(meta.duration_seconds, 1),
        }

        if isinstance(result, (dict, list)):
            formatted["result"] = result
        else:
            formatted["result"] = ToolResultFormatter._truncate_output(str(result))

        return json.dumps(formatted, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def format_result(
        tool_name: str,
        result: Any,
        meta: ExecutionMetadata,
        **kwargs
    ) -> str:
        """统一格式化入口

        Args:
            tool_name: 工具名称
            result: 工具执行结果
            meta: 执行元数据
            **kwargs: 额外参数（如 file_path, path 等）

        Returns:
            格式化后的 JSON 字符串
        """
        # Handle known tool types
        if tool_name in ('shell', 'bash', 'exec'):
            return ToolResultFormatter.format_shell_result(result, meta)

        elif tool_name in ('file_read', 'read', 'FileReadTool'):
            return ToolResultFormatter.format_file_result(
                result, meta, kwargs.get('file_path')
            )

        elif tool_name in ('search', 'grep', 'FileSearchTool'):
            return ToolResultFormatter.format_search_result(result, meta)

        elif tool_name in ('list_dir', 'ls', 'ListDirTool'):
            return ToolResultFormatter.format_list_dir_result(
                result, meta, kwargs.get('path')
            )

        else:
            return ToolResultFormatter.format_generic_result(
                result, meta, tool_name
            )

    @staticmethod
    def _truncate_output(output: str) -> str:
        """截断长输出（使用智能截断策略）

        Args:
            output: 原始输出

        Returns:
            截断后的输出
        """
        return SmartToolResultFormatter.smart_truncate(output)


__all__ = [
    "ToolResultFormatter",
    "SmartToolResultFormatter",
    "ExecutionMetadata",
    "TruncationConfig",
]