"""Shared error handling utilities for model adapters."""
from typing import Any, List, Optional, Tuple, Callable, Awaitable
import httpx
import json
import re

from src.utils import get_logger

logger = get_logger("adapters.errors")

# HTML entity pattern for decoding
_HTML_ENTITY_PATTERN = re.compile(
    r'&(?:amp|lt|gt|quot|apos|#39|#x[0-9a-fA-F]+|#\d+);'
)


def decode_html_entities(value: str) -> str:
    """Decode HTML entities to original characters.

    @param value: String containing HTML entities
    @return: Decoded string
    """
    def replace_entity(match: re.Match) -> str:
        entity = match.group(0)
        replacements = {
            '&amp;': '&',
            '&quot;': '"',
            '&#39;': "'",
            '&apos;': "'",
            '&lt;': '<',
            '&gt;': '>',
        }
        if entity in replacements:
            return replacements[entity]
        hex_match = re.match(r'&#x([0-9a-fA-F]+);', entity)
        if hex_match:
            return chr(int(hex_match.group(1), 16))
        dec_match = re.match(r'&#(\d+);', entity)
        if dec_match:
            return chr(int(dec_match.group(1)))
        return entity
    return _HTML_ENTITY_PATTERN.sub(replace_entity, value)


def decode_html_entities_in_object(obj: Any) -> Any:
    """Recursively decode HTML entities in all strings within an object."""
    if isinstance(obj, str):
        return decode_html_entities(obj)
    if isinstance(obj, list):
        return [decode_html_entities_in_object(item) for item in obj]
    if isinstance(obj, dict):
        return {k: decode_html_entities_in_object(v) for k, v in obj.items()}
    return obj


def extract_balanced_json_prefix(raw: str) -> Optional[str]:
    """Extract balanced JSON prefix (handles trailing extra characters).

    When LLM returns JSON followed by extra text (e.g., explanations),
    this function extracts only the valid JSON part.

    @param raw: Raw string
    @return: Balanced JSON prefix, or None if not found

    @example
        >>> extract_balanced_json_prefix('{"a": 1} some extra')
        '{"a": 1}'
    """
    depth = 0
    in_string = False
    escaped = False
    start = None

    for i, char in enumerate(raw):
        if start is None:
            if char in '{[':
                start = i
                depth = 1
            elif not char.isspace():
                return None
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
            if depth == 0:
                return raw[start:i+1]

    return None


def try_repair_malformed_json(raw: str) -> Optional[dict]:
    """Attempt to repair malformed tool call arguments.

    Uses multiple strategies:
    1. Standard JSON parsing
    2. Extract balanced JSON prefix
    3. Fix unescaped newlines
    4. HTML entity decoding (for xAI/Grok models)
    5. Extract key fields (last resort)

    @param raw: Raw JSON string
    @return: Parsed dictionary, or None if all strategies fail
    """
    if not raw or not raw.strip():
        return None

    raw = raw.strip()

    # Strategy 1: Standard parsing
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract balanced prefix
    prefix = extract_balanced_json_prefix(raw)
    if prefix:
        try:
            return json.loads(prefix)
        except json.JSONDecodeError:
            pass

    # Strategy 3: Fix unescaped newlines
    fixed = _fix_unescaped_newlines(raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Strategy 4: Decode HTML entities (for xAI/Grok models)
    decoded = decode_html_entities(raw)
    if decoded != raw:
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            pass
        # Try with balanced prefix after decoding
        prefix = extract_balanced_json_prefix(decoded)
        if prefix:
            try:
                return json.loads(prefix)
            except json.JSONDecodeError:
                pass

    # Strategy 5: Extract key fields (last resort)
    return _extract_key_fields(raw)


def _fix_unescaped_newlines(raw: str) -> str:
    """Fix unescaped newlines within JSON string values."""
    result = []
    in_string = False
    escaped = False

    for char in raw:
        if not in_string:
            if char == '"':
                in_string = True
            result.append(char)
        else:
            if escaped:
                escaped = False
                result.append(char)
            elif char == '\\':
                escaped = True
                result.append(char)
            elif char == '"':
                in_string = False
                result.append(char)
            elif char == '\n':
                result.append('\\n')
            elif char == '\r':
                pass
            else:
                result.append(char)

    return ''.join(result)


def _extract_key_fields(raw: str) -> Optional[dict]:
    """Extract key fields using regex when JSON parsing fails."""
    result = {}

    patterns = {
        'file_path': r'"file_path"\s*:\s*"([^"]*)"',
        'path': r'"path"\s*:\s*"([^"]*)"',
        'content': r'"content"\s*:\s*"(.*?)"(?=\s*[,}])',
        'command': r'"command"\s*:\s*"([^"]*)"',
        'patch': r'"patch"\s*:\s*"(.*?)"(?=\s*[,}])',
        'pattern': r'"pattern"\s*:\s*"([^"]*)"',
        'dir_path': r'"dir_path"\s*:\s*"([^"]*)"',
    }

    for field, pattern in patterns.items():
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            value = match.group(1)
            value = value.replace('\\n', '\n').replace('\\t', '\t')
            result[field] = value

    return result if result else None


def robust_json_parse(raw_str: str) -> dict:
    """Robustly parse a JSON string that may have escaping issues.

    Three-tier strategy:
    1. Standard JSON parsing
    2. State machine to fix unescaped newlines/quotes within strings
    3. Regex extraction of common fields

    @param raw_str: Raw string that should be JSON
    @return Parsed dictionary or error dict with __parse_error__
    """
    if not raw_str:
        return {"__parse_error__": "Empty raw_arguments"}

    # Strategy 1: Try standard JSON parsing
    try:
        return json.loads(raw_str)
    except json.JSONDecodeError:
        pass

    # Strategy 2: State machine to fix escaping issues
    # The model often returns JSON where code content has literal newlines
    # instead of \n escapes, or has unescaped quotes within strings
    try:
        result = {}
        i = 0
        length = len(raw_str)

        # Simple state machine to find and fix JSON string values
        while i < length:
            # Find a string start
            if raw_str[i] == '"':
                # Mark start of string
                string_start = i
                i += 1
                string_content = []

                # Parse through the string, handling escapes
                while i < length:
                    char = raw_str[i]

                    if char == '\\':
                        # Escaped character - keep as-is
                        string_content.append(raw_str[i:i+2])
                        i += 2
                    elif char == '"':
                        # End of string
                        i += 1
                        break
                    elif char == '\n' or char == '\r':
                        # Unescaped newline in string - fix it
                        string_content.append('\\n')
                        i += 1
                    else:
                        string_content.append(char)
                        i += 1

                # Check what key this string belongs to
                # Look backwards for the key
                key_start = string_start
                while key_start > 0 and raw_str[key_start-1] in ' \t\n\r:':
                    key_start -= 1
                key_end = string_start
                while key_end < len(raw_str) and raw_str[key_end] != ':':
                    key_end += 1
                key = raw_str[key_start:key_end].strip() if (key_end > key_start) else ""

                # Look forward for colon
                value_start = i
                while value_start < length and raw_str[value_start] not in ':':
                    value_start += 1
                value_start += 1  # skip colon

                # Find the end of this value (comma, }, or end)
                value_end = value_start
                while value_end < length and raw_str[value_end] not in ',}':
                    value_end += 1

                # Store the fixed string content
                fixed_str = ''.join(string_content)
                if key in ('file_path', 'path', 'pattern', 'command'):
                    result[key] = fixed_str
                elif key == 'content' and 'content' not in result:
                    # For content, might appear multiple times - take first substantial one
                    result['content'] = fixed_str

            i += 1

        if result and len(result) >= 2:  # Require at least file_path and content
            logger.debug(f"[errors] robust_json_parse: state machine parsed: {list(result.keys())}")
            return result

    except Exception as e:
        logger.debug(f"[errors] robust_json_parse: state machine failed: {e}")

    # Strategy 3: Try to extract fields using regex for common tool schemas
    try:
        result = {}

        # Try to extract file_path
        file_path_match = re.search(r'"file_path"\s*:\s*"([^"]*)"', raw_str)
        if file_path_match:
            result["file_path"] = file_path_match.group(1)

        # Try to extract content - more robust pattern that handles newlines
        content_match = re.search(r'"content"\s*:\s*"(.*?)"(?=\s*[,\}])', raw_str, re.DOTALL)
        if content_match:
            result["content"] = content_match.group(1)

        # Try to extract command
        cmd_match = re.search(r'"command"\s*:\s*"([^"]*)"', raw_str)
        if cmd_match:
            result["command"] = cmd_match.group(1)

        # Try to extract patch
        patch_match = re.search(r'"patch"\s*:\s*"(.*?)"(?=\s*[,\}])', raw_str, re.DOTALL)
        if patch_match:
            result["patch"] = patch_match.group(1)

        # Try to extract path
        path_match = re.search(r'"path"\s*:\s*"([^"]*)"', raw_str)
        if path_match:
            result["path"] = path_match.group(1)

        # Try to extract pattern
        pattern_match = re.search(r'"pattern"\s*:\s*"([^"]*)"', raw_str)
        if pattern_match:
            result["pattern"] = pattern_match.group(1)

        # Try to extract dir_path
        dir_path_match = re.search(r'"dir_path"\s*:\s*"([^"]*)"', raw_str)
        if dir_path_match:
            result["dir_path"] = dir_path_match.group(1)

        if result:
            logger.debug(f"[errors] robust_json_parse: regex extracted: {list(result.keys())}")
            return result

    except Exception as e:
        logger.debug(f"[errors] robust_json_parse: regex extraction failed: {e}")

    # All strategies failed
    logger.warning(f"[errors] robust_json_parse: all strategies failed")
    return {
        "__parse_error__": f"无法解析 JSON (长度={len(raw_str)}): {raw_str[:200]}...",
        "__raw_original__": raw_str[:2000]  # 保存原始内容供调试
    }


def validate_openai_response(result: dict, context: str = "") -> None:
    """Validate OpenAI-compatible API response structure.

    @param result: API response dictionary
    @param context: Context string for error messages (e.g., "OpenAI", "LMStudio")
    @raises ValueError: If response structure is invalid
    """
    prefix = f"Invalid API response {context}: " if context else "Invalid API response: "

    if not result.get("choices") or not result["choices"]:
        logger.error(f"{prefix}missing choices | response={str(result)[:200]}")
        raise ValueError(f"{prefix}missing choices")

    choice = result["choices"][0]
    if "message" not in choice:
        logger.error(f"{prefix}missing message | response={str(result)[:200]}")
        raise ValueError(f"{prefix}missing message")

    if "content" not in choice["message"]:
        logger.error(f"{prefix}missing content | response={str(result)[:200]}")
        raise ValueError(f"{prefix}missing content")


def handle_http_errors(
    e: httpx.HTTPStatusError,
    adapter_name: str,
    api_key_label: str = "API key"
) -> None:
    """Handle common HTTP errors with user-friendly messages.

    @param e: HTTPStatusError exception
    @param adapter_name: Name of the adapter for logging (e.g., "OpenAI", "Ollama")
    @param api_key_label: Label for the API key in error messages
    @raises ConnectionError: For 401/404 errors
    @raises: Re-raises other HTTP errors
    """
    if e.response.status_code == 401:
        logger.error(f"{adapter_name} API 认证失败 | 请检查 {api_key_label}")
        raise ConnectionError(
            f"Authentication failed. Please check your {api_key_label} in config.yaml"
        ) from None

    if e.response.status_code == 404:
        logger.error(f"{adapter_name} API 端点未找到")
        raise ConnectionError(
            f"API endpoint not found. Please check your base_url in config.yaml"
        ) from None

    # Re-raise for other HTTP errors
    logger.error(f"{adapter_name} HTTP 错误 | status={e.response.status_code} | {e}")
    raise


async def check_tool_call_parse_errors_and_retry(
    tool_calls: list[dict],
    fallback_func: Callable[[], Awaitable[Tuple[str, list[dict]]]]
) -> Optional[Tuple[str, list[dict]]]:
    """Check if any tool call has parse errors and trigger retry.

    @param tool_calls: List of tool calls
    @param fallback_func: Async function to call for retry (e.g., prompt injection)
    @return Tuple of (response, tool_calls) if retry triggered, None otherwise
    """
    for tc in tool_calls:
        if "__parse_error__" in tc.get("arguments", {}):
            logger.warning(f"检测到工具参数解析错误，尝试 prompt injection 重试")
            return await fallback_func()
    return None


def extract_tool_calls_from_message(message: dict) -> list[dict]:
    """Extract tool calls from OpenAI-style response message.

    Uses try_repair_malformed_json for better handling of malformed JSON.

    @param message: Response message dict
    @return List of tool calls with name, arguments, id
    """
    tool_calls = []
    for call in message.get("tool_calls", []):
        func = call.get("function", {})
        args = func.get("arguments", "{}")

        if isinstance(args, str):
            # Try repair strategies first
            parsed = try_repair_malformed_json(args)
            if parsed is not None:
                args = parsed
            else:
                # Fall back to simple json.loads + raw on failure
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}

        tool_calls.append({
            "name": func.get("name"),
            "arguments": args,
            "id": call.get("id"),
        })
    return tool_calls
