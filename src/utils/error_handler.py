"""Tool call error handler and reporting."""
import logging
from typing import Optional
from dataclasses import dataclass

from src.utils import get_logger

logger = get_logger("utils.error_handler")


@dataclass
class ParseErrorReport:
    """解析错误报告"""
    tool_name: str
    raw_input: str
    error_message: str
    strategies_tried: list
    final_result: dict


class ToolCallErrorHandler:
    """工具调用错误处理器"""

    def __init__(self):
        self.logger = logging.getLogger("Nexus.tool_call")

    def handle_parse_error(
        self,
        raw_args: str,
        error: Exception,
        tool_name: str,
        strategies: list = None
    ) -> dict:
        """处理参数解析错误

        @param raw_args: 原始参数字符串
        @param error: 解析错误
        @param tool_name: 工具名称
        @param strategies: 已尝试的修复策略
        @return: 处理后的结果
        """
        strategies = strategies or []

        # 记录详细错误信息
        self.logger.warning(
            f"工具调用参数解析失败 | "
            f"tool={tool_name} | "
            f"error={type(error).__name__}: {error} | "
            f"raw_length={len(raw_args)} | "
            f"raw_preview={self._safe_preview(raw_args, 200)} | "
            f"strategies={strategies}"
        )

        # 构建错误报告
        report = ParseErrorReport(
            tool_name=tool_name,
            raw_input=raw_args[:2000],  # 保留前2000字符用于调试
            error_message=str(error),
            strategies_tried=strategies,
            final_result={"__raw__": raw_args[:1000], "__error__": str(error)}
        )

        # 可以在这里发送错误报告到监控系统
        # self._send_error_report(report)

        return report.final_result

    @staticmethod
    def _safe_preview(text: str, max_len: int) -> str:
        """生成安全的预览字符串

        @param text: 原始文本
        @param max_len: 最大长度
        @return: 安全预览字符串
        """
        if len(text) <= max_len:
            return text.replace('\n', '\\n')
        return text[:max_len].replace('\n', '\\n') + "..."


# Global instance
_tool_call_error_handler: Optional[ToolCallErrorHandler] = None


def get_tool_call_error_handler() -> ToolCallErrorHandler:
    """Get global tool call error handler instance"""
    global _tool_call_error_handler
    if _tool_call_error_handler is None:
        _tool_call_error_handler = ToolCallErrorHandler()
    return _tool_call_error_handler
