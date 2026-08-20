"""Error handling: JSON repair, classification, retry/backoff, recovery."""
import httpx
import pytest

from src.error.json_repair import (
    try_repair_malformed_json,
    robust_json_parse,
    extract_balanced_json_prefix,
    decode_html_entities,
    validate_openai_response,
    handle_http_errors,
    extract_tool_calls_from_message,
    check_tool_call_parse_errors_and_retry,
)
from src.error.classifier import APIErrorClassifier, ErrorType
from src.error.recovery import ErrorRecovery, RecoveryStrategy
from src.error.retry import execute_with_retry
from src.error.constants import BACKOFF_BASE_DELAY, BACKOFF_MAX_DELAY


class TestJsonRepair:
    def test_valid_json_passthrough(self):
        assert try_repair_malformed_json('{"a": 1}') == {"a": 1}

    def test_truncated_json_key_field_fallback(self):
        # truncated JSON with known tool fields -> last-resort key extraction
        result = try_repair_malformed_json('{"file_path": "a.txt", "content": "hel')
        assert result == {"file_path": "a.txt"}

    def test_truncated_json_without_known_fields_returns_none(self):
        assert try_repair_malformed_json('{"a": 1, "b": 2') is None

    def test_json_followed_by_extra_text(self):
        result = extract_balanced_json_prefix('{"a": 1} trailing explanation')
        assert result == '{"a": 1}'

    def test_html_entity_repair(self):
        result = try_repair_malformed_json('{&quot;a&quot;: 1}')
        assert result == {"a": 1}

    def test_unescaped_newline_repair(self):
        raw = '{"content": "line1\nline2"}'
        result = try_repair_malformed_json(raw)
        assert result is not None
        assert "line1" in result["content"]

    def test_unrepairable_returns_none(self):
        assert try_repair_malformed_json("not json at all !!!") is None

    def test_robust_json_parse_error_marker(self):
        parsed = robust_json_parse("{{{{")
        assert "__parse_error__" in parsed

    def test_robust_json_parse_valid(self):
        assert robust_json_parse('{"x": "y"}') == {"x": "y"}

    def test_decode_html_entities(self):
        assert decode_html_entities("&lt;tag&gt;") == "<tag>"


class TestValidateAndHttp:
    def test_validate_openai_response_ok(self):
        validate_openai_response(
            {"choices": [{"message": {"content": "hi"}}]}
        )

    def test_validate_openai_response_missing_choices(self):
        with pytest.raises(ValueError):
            validate_openai_response({})

    def test_validate_openai_response_missing_content(self):
        with pytest.raises(ValueError):
            validate_openai_response({"choices": [{"message": {}}]})

    @staticmethod
    def _http_error(status_code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://test.local")
        response = httpx.Response(status_code, request=request)
        return httpx.HTTPStatusError("err", request=request, response=response)

    def test_handle_http_401_raises_connection_error(self):
        with pytest.raises(ConnectionError):
            handle_http_errors(self._http_error(401), "Test")

    def test_handle_http_404_raises_connection_error(self):
        with pytest.raises(ConnectionError):
            handle_http_errors(self._http_error(404), "Test")

    def test_handle_http_500_reraises(self):
        # handle_http_errors re-raises via bare `raise`, so call it from an except block
        try:
            raise self._http_error(500)
        except httpx.HTTPStatusError as e:
            with pytest.raises(httpx.HTTPStatusError):
                handle_http_errors(e, "Test")


class TestToolCallExtraction:
    def test_extract_from_openai_message(self):
        message = {
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "file_read", "arguments": '{"path": "a"}'},
            }]
        }
        calls = extract_tool_calls_from_message(message)
        assert calls == [{
            "name": "file_read",
            "arguments": {"path": "a"},
            "id": "call_1",
        }]

    def test_extract_keeps_raw_on_unrepairable_args(self):
        message = {
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "t", "arguments": '{"a": 1, "b": 2'},
            }]
        }
        calls = extract_tool_calls_from_message(message)
        assert calls[0]["arguments"] == {"raw": '{"a": 1, "b": 2'}

    def test_extract_repairs_args_with_known_fields(self):
        message = {
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": "t", "arguments": '{"file_path": "a.txt", "x": '},
            }]
        }
        calls = extract_tool_calls_from_message(message)
        assert calls[0]["arguments"] == {"file_path": "a.txt"}

    async def test_check_parse_errors_triggers_fallback(self):
        called = {"flag": False}

        async def fallback():
            called["flag"] = True
            return ("fallback", [])

        tool_calls = [{"name": "t", "arguments": {"__parse_error__": "bad"}}]
        result = await check_tool_call_parse_errors_and_retry(tool_calls, fallback)
        assert called["flag"] and result == ("fallback", [])

    async def test_check_parse_errors_no_fallback_when_clean(self):
        async def fallback():
            raise AssertionError("should not be called")

        result = await check_tool_call_parse_errors_and_retry(
            [{"name": "t", "arguments": {"a": 1}}], fallback
        )
        assert result is None


class TestClassifier:
    def test_status_code_map(self):
        assert APIErrorClassifier.STATUS_CODE_MAP[401] == ErrorType.AUTHENTICATION
        assert APIErrorClassifier.STATUS_CODE_MAP[404] == ErrorType.NOT_FOUND
        assert APIErrorClassifier.STATUS_CODE_MAP[429] == ErrorType.RATE_LIMIT

    def test_from_exception_connection_is_transient(self):
        assert APIErrorClassifier.from_exception(ConnectionError("x")) == ErrorType.TRANSIENT

    def test_from_exception_timeout_is_transient(self):
        assert APIErrorClassifier.from_exception(TimeoutError("x")) == ErrorType.TRANSIENT

    def test_from_exception_auth_message(self):
        err = Exception("HTTP 401 Unauthorized")
        assert APIErrorClassifier.from_exception(err) == ErrorType.AUTHENTICATION

    def test_classify_and_get_strategy_backoff_for_transient(self):
        error_type, strategy = APIErrorClassifier.classify_and_get_strategy(
            ConnectionError("network")
        )
        assert error_type == ErrorType.TRANSIENT
        assert strategy == RecoveryStrategy.BACKOFF_RETRY


class TestBackoffAndRetry:
    def test_backoff_delay_bounds(self):
        for attempt in range(6):
            delay = ErrorRecovery.calculate_backoff_delay(attempt)
            base = min(BACKOFF_BASE_DELAY * (2 ** attempt), BACKOFF_MAX_DELAY)
            assert base <= delay <= base + 1.0

    async def test_handle_max_tokens_injects_continuation(self):
        messages = [{"role": "user", "content": "hi"}]
        should_retry, count = await ErrorRecovery.handle_max_tokens(messages, 0)
        assert should_retry is True
        assert count == 1
        assert messages[-1]["role"] == "user"
        assert len(messages) == 2

    async def test_execute_with_retry_success_first_try(self):
        async def fn():
            return 42

        result, success = await execute_with_retry(fn)
        assert (result, success) == (42, True)

    async def test_execute_with_retry_non_retryable_stops_immediately(self):
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            raise ConnectionError("HTTP 401 auth failed")

        result, success = await execute_with_retry(fn, max_retries=3)
        assert success is False and result is None
        # AUTHENTICATION -> not BACKOFF_RETRY, so no retry happens
        assert attempts["n"] == 1
