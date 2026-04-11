"""Tool dependency analyzer for parallel execution"""
from typing import List, Set, Dict, Optional, TYPE_CHECKING

from dataclasses import dataclass, field

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: dict
    depends_on: Set[str] = field(default_factory=set)


class DependencyAnalyzer:
    """
    工具调用依赖分析器

    分析工具调用之间的依赖关系，确定哪些可以并行执行。
    支持两种模式：
    1. 显式依赖：如果 tool_calls 中包含 depends_on 字段，使用拓扑排序
    2. 隐式依赖：否则使用读/写工具类型分组

    使用 Tool.concurrency_category 属性进行动态分类，
    替代之前的硬编码字符串集合。
    """

    def __init__(self, tool_registry: Optional["ToolRegistry"] = None):
        """Initialize with optional tool registry for dynamic classification.

        Args:
            tool_registry: Optional registry to query tool properties for classification
        """
        self._tool_registry = tool_registry
        self._classification_cache: Dict[str, str] = {}

    # 回退用的读工具集合（用于未知工具的启发式判断）
    _READ_TOOLS_FALLBACK = frozenset({
        'file_read', 'search', 'list_dir', 'grep', 'read', 'glob',
        'find', 'stat', 'read_file', 'dir', 'walk', 'check_background',
        'check_subagent', 'todo_read', 'get_tasks', 'get_teams',
    })
    # 回退用的写工具集合
    _WRITE_TOOLS_FALLBACK = frozenset({
        'file_write', 'file_patch', 'shell', 'bash', 'write', 'patch',
        'edit', 'delete', 'rm', 'mkdir', 'move', 'rename',
        'create_directory', 'remove_directory', 'background_run',
        'cancel_subagent', 'todo_write', 'create_task', 'update_task',
    })

    def analyze(self, tool_calls: List[dict]) -> List[List[dict]]:
        """
        分析依赖关系，返回可并行执行的批次

        Args:
            tool_calls: 原始工具调用列表 [{"name": ..., "arguments": ..., "id": ...}, ...]

        Returns:
            批次列表，每个批次内的工具调用可以并行执行
        """
        if not tool_calls:
            return []

        if len(tool_calls) == 1:
            return [tool_calls]

        # Check if any tool_call has explicit depends_on
        has_explicit_deps = any(tc.get("depends_on") for tc in tool_calls)

        if has_explicit_deps:
            return self._analyze_with_dependencies(tool_calls)
        else:
            return self._analyze_by_type(tool_calls)

    def _get_tool_classification(self, tool_name: str) -> str:
        """Get concurrency classification for a tool.

        Uses the tool's concurrency_category property if available,
        falls back to heuristics for unknown tools.
        Results are cached for repeated lookups.

        Args:
            tool_name: Name of the tool

        Returns:
            'read', 'write', or 'other'
        """
        # Check cache first
        if tool_name in self._classification_cache:
            return self._classification_cache[tool_name]

        # Try to get tool from registry
        tool = None
        if self._tool_registry:
            tool = self._tool_registry.get(tool_name)

        # Use tool property if available
        if tool and hasattr(tool, 'concurrency_category'):
            classification = tool.concurrency_category
        else:
            # Fallback to heuristics for unknown tools
            classification = self._classify_unknown_tool(tool_name)

        # Cache the result
        self._classification_cache[tool_name] = classification
        return classification

    def _classify_unknown_tool(self, tool_name: str) -> str:
        """Classify unknown tools based on name heuristics.

        Args:
            tool_name: Tool name to classify

        Returns:
            'read', 'write', or 'other'
        """
        # Check against fallback sets
        if tool_name in self._READ_TOOLS_FALLBACK:
            return "read"
        if tool_name in self._WRITE_TOOLS_FALLBACK:
            return "write"

        # Unknown tool - be conservative and treat as write
        return "other"

    def _analyze_by_type(self, tool_calls: List[dict]) -> List[List[dict]]:
        """按工具类型分组（读可以并行，写需要串行）

        使用 Tool.concurrency_category 属性进行动态分类。
        """
        read_calls = []
        write_calls = []
        other_calls = []

        for tc in tool_calls:
            tool_name = tc.get("name", "")
            classification = self._get_tool_classification(tool_name)

            if classification == "read":
                read_calls.append(tc)
            elif classification == "write":
                write_calls.append(tc)
            else:
                # 'other' - 未知或保守分类，视为需要串行
                other_calls.append(tc)

        batches = []

        # 第一批：所有读操作并行
        if read_calls:
            batches.append(read_calls)

        # 后续批次：每个写操作/其他操作单独一批（串行执行）
        for wc in write_calls + other_calls:
            batches.append([wc])

        return batches

    def _analyze_with_dependencies(self, tool_calls: List[dict]) -> List[List[dict]]:
        """使用显式依赖进行拓扑排序"""
        # Build dependency graph
        id_to_tc = {tc.get("id"): tc for tc in tool_calls}
        in_degree: Dict[str, int] = {tc.get("id"): 0 for tc in tool_calls}
        adj_list: Dict[str, List[str]] = {tc.get("id"): [] for tc in tool_calls}

        # Build edges from depends_on
        for tc in tool_calls:
            tc_id = tc.get("id")
            deps = tc.get("depends_on", [])
            for dep_id in deps:
                if dep_id in id_to_tc:
                    adj_list[dep_id].append(tc_id)
                    in_degree[tc_id] += 1

        # Topological sort using Kahn's algorithm
        batches = []
        processed: Set[str] = set()

        while len(processed) < len(tool_calls):
            # Find all nodes with in_degree == 0
            ready = [tc for tc in tool_calls
                     if tc.get("id") not in processed and in_degree.get(tc.get("id"), 0) == 0]

            if not ready:
                # Circular dependency or error - fall back to serial execution
                remaining = [tc for tc in tool_calls if tc.get("id") not in processed]
                for tc in remaining:
                    batches.append([tc])
                    processed.add(tc.get("id"))
                break

            batches.append(ready)
            processed.update(tc.get("id") for tc in ready)

            # Update in_degrees
            for tc in ready:
                tc_id = tc.get("id")
                for neighbor in adj_list.get(tc_id, []):
                    in_degree[neighbor] -= 1

        return batches

    def can_parallel(self, tool_calls: List[dict]) -> bool:
        """检查是否可以并行执行所有工具调用"""
        if not tool_calls:
            return True
        if len(tool_calls) <= 1:
            return True

        # 如果都是读工具，可以并行
        return all(
            self._get_tool_classification(tc.get("name", "")) == "read"
            for tc in tool_calls
        )
