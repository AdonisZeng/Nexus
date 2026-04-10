"""Fine-grained tool parameter validation for subagent restrictions"""
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from src.utils import get_logger

logger = get_logger("subagent.param_validator")


class ParameterConstraint(ABC):
    """参数约束基类"""

    @abstractmethod
    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        """
        验证值是否满足约束。

        Returns:
            (is_valid, error_message)
        """
        pass

    @classmethod
    def from_dict(cls, constraint: dict) -> "ParameterConstraint":
        """
        从配置字典创建合适的约束类型。

        Args:
            constraint: 约束配置字典

        Returns:
            对应的约束实例
        """
        if "max_length" in constraint:
            return MaxLengthConstraint(constraint["max_length"])
        if "min_length" in constraint:
            return MinLengthConstraint(constraint["min_length"])
        if "dangerous_flags" in constraint:
            return DangerousFlagsConstraint(constraint["dangerous_flags"])
        if "allowed_values" in constraint:
            return AllowedValuesConstraint(constraint["allowed_values"])
        if "pattern" in constraint:
            return PatternConstraint(constraint["pattern"])
        if "max_value" in constraint:
            return MaxValueConstraint(constraint["max_value"])
        if "min_value" in constraint:
            return MinValueConstraint(constraint["min_value"])
        return NoOpConstraint()


class NoOpConstraint(ParameterConstraint):
    """空操作约束，始终通过"""

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        return True, None


class MaxLengthConstraint(ParameterConstraint):
    """最大长度约束"""

    def __init__(self, max_length: int):
        self.max_length = max_length

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        if not isinstance(value, str):
            return True, None
        if len(value) > self.max_length:
            return False, f"Parameter exceeds max_length ({self.max_length})"
        return True, None


class MinLengthConstraint(ParameterConstraint):
    """最小长度约束"""

    def __init__(self, min_length: int):
        self.min_length = min_length

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        if not isinstance(value, str):
            return True, None
        if len(value) < self.min_length:
            return False, f"Parameter below min_length ({self.min_length})"
        return True, None


class DangerousFlagsConstraint(ParameterConstraint):
    """危险标志检测约束"""

    def __init__(self, dangerous_flags: list[str]):
        self.dangerous_flags = [f.lower() for f in dangerous_flags]

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        if not isinstance(value, str):
            return True, None
        value_lower = value.lower()
        for flag in self.dangerous_flags:
            if flag in value_lower:
                return False, f"Parameter contains dangerous flag: '{flag}'"
        return True, None


class AllowedValuesConstraint(ParameterConstraint):
    """允许值约束"""

    def __init__(self, allowed_values: list[Any]):
        self.allowed_values = allowed_values

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        if value not in self.allowed_values:
            return False, f"Parameter value not in allowed list: {self.allowed_values}"
        return True, None


class PatternConstraint(ParameterConstraint):
    """正则表达式模式约束"""

    def __init__(self, pattern: str):
        try:
            self.pattern = re.compile(pattern)
        except re.error as e:
            logger.warning(f"[PatternConstraint] Invalid pattern '{pattern}': {e}")
            self.pattern = None

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        if not isinstance(value, str):
            return True, None
        if self.pattern is None:
            return True, None
        if not self.pattern.search(value):
            return False, f"Parameter does not match required pattern: {self.pattern.pattern}"
        return True, None


class MaxValueConstraint(ParameterConstraint):
    """数值最大约束"""

    def __init__(self, max_value: float):
        self.max_value = max_value

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        try:
            num_value = float(value) if isinstance(value, str) else value
            if num_value > self.max_value:
                return False, f"Parameter exceeds max_value ({self.max_value})"
        except (ValueError, TypeError):
            return False, f"Parameter is not a valid number"
        return True, None


class MinValueConstraint(ParameterConstraint):
    """数值最小约束"""

    def __init__(self, min_value: float):
        self.min_value = min_value

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        try:
            num_value = float(value) if isinstance(value, str) else value
            if num_value < self.min_value:
                return False, f"Parameter below min_value ({self.min_value})"
        except (ValueError, TypeError):
            return False, f"Parameter is not a valid number"
        return True, None


class ToolParameterValidator:
    """
    工具参数验证器。

    在 SubagentRunner 中使用，对工具参数进行细粒度限制。

    示例配置:
    ```yaml
    tool-parameters:
      bash:
        command:
          max_length: 2000
          dangerous_flags:
            - "rm -rf"
            - "sudo"
            - "> /dev/null"
      file_write:
        content:
          max_length: 100000
    ```
    """

    def __init__(self, tool_parameters: Optional[dict[str, dict[str, Any]]] = None):
        """
        初始化验证器。

        Args:
            tool_parameters: 工具参数约束配置
                              格式: {tool_name: {param_name: constraint_dict}}
        """
        self._constraints: dict[str, dict[str, ParameterConstraint]] = {}
        if tool_parameters:
            self._build_constraints(tool_parameters)

    def _build_constraints(self, tool_parameters: dict[str, dict[str, Any]]) -> None:
        """从配置构建约束对象"""
        for tool_name, params in tool_parameters.items():
            if not isinstance(params, dict):
                continue
            self._constraints[tool_name] = {}
            for param_name, constraint_def in params.items():
                if isinstance(constraint_def, dict):
                    self._constraints[tool_name][param_name] = ParameterConstraint.from_dict(constraint_def)
                else:
                    self._constraints[tool_name][param_name] = NoOpConstraint()

    def validate(
        self,
        tool_name: str,
        args: dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        验证工具参数是否满足约束。

        Args:
            tool_name: 工具名称
            args: 工具参数字典

        Returns:
            (is_valid, error_message)
        """
        if tool_name not in self._constraints:
            return True, None

        for param_name, constraint in self._constraints[tool_name].items():
            if param_name not in args:
                continue

            value = args[param_name]
            is_valid, error = constraint.validate(value)
            if not is_valid:
                logger.warning(
                    f"[ToolParameterValidator] {tool_name}.{param_name} validation failed: {error}"
                )
                return False, f"{tool_name}.{param_name}: {error}"

        return True, None

    def get_constraints_for_tool(self, tool_name: str) -> dict[str, ParameterConstraint]:
        """
        获取指定工具的所有约束。

        Args:
            tool_name: 工具名称

        Returns:
            参数名到约束的字典
        """
        return self._constraints.get(tool_name, {})

    def has_constraints(self, tool_name: str) -> bool:
        """检查指定工具是否有约束"""
        return tool_name in self._constraints and bool(self._constraints[tool_name])


__all__ = [
    "ParameterConstraint",
    "NoOpConstraint",
    "MaxLengthConstraint",
    "MinLengthConstraint",
    "DangerousFlagsConstraint",
    "AllowedValuesConstraint",
    "PatternConstraint",
    "MaxValueConstraint",
    "MinValueConstraint",
    "ToolParameterValidator",
]
