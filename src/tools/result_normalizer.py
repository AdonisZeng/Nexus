"""Tool Result Normalizer - 统一工具输出格式

统一所有工具 (native + MCP) 的输出格式，
确保 LLM 收到一致的结果。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.tools.result_formatter import SmartToolResultFormatter, TruncationConfig

logger = logging.getLogger("Nexus")


@dataclass
class NormalizedToolResult:
    """归一化的工具结果"""
    source: str                    # "native" 或 "mcp"
    server: Optional[str]          # MCP 服务器名 (如果是 MCP 工具)
    tool: str                      # 工具名
    status: str                    # "ok" 或 "error"
    output: str                    # 输出内容
    preview: str                   # 预览 (截断后)
    is_truncated: bool             # 是否被截断
    duration_seconds: Optional[float] = None


class ToolResultNormalizer:
    """工具结果归一化器

    统一所有工具 (native + MCP) 的输出格式，
    确保 LLM 收到一致的结果。

    输出格式:
    {
        "source": "mcp" | "native",
        "server": "<server_name>" | null,
        "tool": "<tool_name>",
        "status": "ok" | "error",
        "output": "<full_output>",
        "preview": "<truncated_output>",
        "is_truncated": true | false,
        "duration_seconds": <seconds> | null
    }
    """

    MAX_PREVIEW_CHARS = 500
    MAX_FULL_OUTPUT_CHARS = 8000

    @classmethod
    def normalize(
        cls,
        tool_name: str,
        result: Any,
        intent: Optional[dict] = None,
    ) -> str:
        """归一化工具结果

        Args:
            tool_name: 工具名称
            result: 原始结果
            intent: 标准化意图 (来自 CapabilityPermissionGate.normalize)

        Returns:
            JSON 格式字符串
        """
        intent = intent or {}

        # 解析工具来源
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            source = "mcp"
            server = parts[1] if len(parts) > 1 else None
            tool = parts[2] if len(parts) > 2 else tool_name
        else:
            source = "native"
            server = None
            tool = tool_name

        # 转换结果为字符串
        if isinstance(result, str):
            output = result
        elif isinstance(result, (dict, list)):
            try:
                output = json.dumps(result, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                output = str(result)
        else:
            output = str(result)

        # 判断状态
        output_lower = output.lower()
        is_error = (
            isinstance(result, dict) and "error" in result
        ) or (
            output.startswith("Error:")
            or "error" in output_lower[:100]
            or "exception" in output_lower[:100]
            and "traceback" not in output_lower
        )
        status = "error" if is_error else "ok"

        # 截断预览
        preview = SmartToolResultFormatter.smart_truncate(
            output,
            config=TruncationConfig(max_chars=cls.MAX_PREVIEW_CHARS),
        )

        normalized = NormalizedToolResult(
            source=source,
            server=server,
            tool=tool,
            status=status,
            output=output[:cls.MAX_FULL_OUTPUT_CHARS],
            preview=preview,
            is_truncated=len(output) > cls.MAX_FULL_OUTPUT_CHARS,
        )

        return cls._to_json(normalized)

    @classmethod
    def _to_json(cls, result: NormalizedToolResult) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(
            {
                "source": result.source,
                "server": result.server,
                "tool": result.tool,
                "status": result.status,
                "output": result.output,
                "preview": result.preview,
                "is_truncated": result.is_truncated,
                "duration_seconds": result.duration_seconds,
            },
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def normalize_for_mcp(
        cls,
        server: str,
        tool: str,
        mcp_result: Any,
    ) -> str:
        """专门为 MCP 工具归一化结果

        Args:
            server: MCP 服务器名称
            tool: 工具名称
            mcp_result: MCP 服务器返回的结果

        Returns:
            JSON 格式字符串
        """
        qualified_name = f"mcp__{server}__{tool}"
        intent = {
            "source": "mcp",
            "server": server,
            "tool": tool,
        }
        return cls.normalize(qualified_name, mcp_result, intent)

    @classmethod
    def extract_error(cls, result: Any) -> Optional[str]:
        """从结果中提取错误信息

        Args:
            result: 工具结果

        Returns:
            错误信息或 None
        """
        if isinstance(result, dict):
            return result.get("error")
        if isinstance(result, str):
            if result.startswith("Error:"):
                return result
            if "error" in result.lower()[:100]:
                return result
        return None
