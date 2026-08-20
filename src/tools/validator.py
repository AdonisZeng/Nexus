"""Tool call argument validator."""
from typing import Any, Optional, Tuple

from src.utils import get_logger

logger = get_logger("tools.validator")


class ToolCallValidator:
    """Tool call argument validator"""

    @staticmethod
    def validate_arguments(
        tool_name: str,
        args: dict,
        schema: dict
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Validate tool arguments against JSON Schema.

        @param tool_name: Tool name
        @param args: Arguments dictionary
        @param schema: JSON Schema
        @return: (is_valid, error_message, fixed_args)
        """
        try:
            # Check required fields
            required = schema.get('required', [])
            for field in required:
                if field not in args:
                    return False, f"缺少必需字段: {field}", None

            # Check field types
            properties = schema.get('properties', {})
            fixed_args = dict(args)

            for key, value in args.items():
                if key in properties:
                    expected_type = properties[key].get('type')
                    is_valid, fixed_value = ToolCallValidator._validate_and_fix_type(
                        value, expected_type
                    )

                    if not is_valid:
                        return False, f"字段 '{key}' 类型错误，期望 {expected_type}", None
                    if fixed_value is not None:
                        fixed_args[key] = fixed_value

            return True, None, fixed_args

        except Exception as e:
            logger.warning(f"[validator] {tool_name} 参数验证异常: {e}")
            return False, str(e), None

    @staticmethod
    def _validate_and_fix_type(
        value: Any,
        expected_type: str
    ) -> Tuple[bool, Optional[Any]]:
        """Validate and attempt to fix type mismatches.

        @return: (is_valid, fixed_value)
        """
        type_map = {
            'string': str,
            'integer': int,
            'number': (int, float),
            'boolean': bool,
            'array': list,
            'object': dict,
        }

        expected = type_map.get(expected_type)
        if not expected:
            return True, None  # Unknown type, skip validation

        # Type matches
        if isinstance(value, expected):
            return True, None

        # Try type conversion
        if expected_type == 'string' and not isinstance(value, str):
            return True, str(value)

        if expected_type == 'integer' and isinstance(value, str):
            try:
                return True, int(value)
            except ValueError:
                pass

        if expected_type == 'number' and isinstance(value, str):
            try:
                return True, float(value)
            except ValueError:
                pass

        if expected_type == 'boolean' and isinstance(value, str):
            lower = value.lower()
            if lower in ('true', '1', 'yes'):
                return True, True
            elif lower in ('false', '0', 'no'):
                return True, False

        return False, None
