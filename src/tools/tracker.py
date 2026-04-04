"""
@file tracker.py
@brief 工具调用跟踪器模块

提供 ToolCall 数据类和 ToolCallTracker 类，用于记录和跟踪工具调用的历史记录。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ToolCall:
    """
    @brief 工具调用记录数据类

    存储单次工具调用的完整信息，包括工具名称、参数、结果、时间戳和执行状态。
    """
    tool_name: str
    args: dict
    result: Any
    timestamp: datetime
    success: bool


class ToolCallTracker:
    """
    @brief 工具调用跟踪器类

    用于记录、管理和查询工具调用的历史记录。
    提供添加记录、生成摘要、获取记录列表和清空记录等功能。
    """

    def __init__(self):
        """
        @brief 初始化跟踪器

        创建一个空列表用于存储工具调用记录。
        """
        self._calls: list[ToolCall] = []

    def record(self, tool_name: str, args: dict, result: Any, success: bool) -> None:
        """
        @brief 记录一次工具调用

        @param tool_name 工具名称
        @param args 调用参数字典
        @param result 调用结果
        @param success 是否执行成功
        """
        call = ToolCall(
            tool_name=tool_name,
            args=args,
            result=result,
            timestamp=datetime.now(),
            success=success
        )
        self._calls.append(call)

    def get_summary(self) -> str:
        """
        @brief 生成调用摘要

        统计各工具调用次数及成功/失败情况，生成格式化的摘要字符串。

        @return 格式化的调用摘要字符串
        """
        if not self._calls:
            return "Tool Calls Summary:\nNo calls recorded."

        stats: dict[str, dict[str, int]] = {}
        for call in self._calls:
            if call.tool_name not in stats:
                stats[call.tool_name] = {"total": 0, "success": 0, "failed": 0}
            stats[call.tool_name]["total"] += 1
            if call.success:
                stats[call.tool_name]["success"] += 1
            else:
                stats[call.tool_name]["failed"] += 1

        lines = ["Tool Calls Summary:"]
        for tool_name, stat in sorted(stats.items()):
            lines.append(
                f"- {tool_name}: {stat['total']} call{'s' if stat['total'] > 1 else ''} "
                f"({stat['success']} success, {stat['failed']} failed)"
            )

        total = len(self._calls)
        total_success = sum(1 for call in self._calls if call.success)
        total_failed = total - total_success
        lines.append(f"Total: {total} call{'s' if total > 1 else ''} ({total_success} success, {total_failed} failed)")

        return "\n".join(lines)

    def get_calls(self) -> list[ToolCall]:
        """
        @brief 获取所有调用记录

        @return 工具调用记录列表的副本
        """
        return self._calls.copy()

    def clear(self) -> None:
        """
        @brief 清空所有调用记录
        """
        self._calls.clear()
