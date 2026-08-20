"""Permissions: modes, registry classification, checker, ToolGate lock."""
import asyncio

import pytest

from src.permissions import (
    PermissionMode,
    PermissionRegistry,
    MUTATING_TOOLS,
    SAFE_TOOLS,
    PermissionChecker,
    PermissionResult,
    ToolGate,
)


class TestPermissionMode:
    def test_from_string_values(self):
        assert PermissionMode.from_string("normal") == PermissionMode.NORMAL
        assert PermissionMode.from_string("read_only") == PermissionMode.READ_ONLY
        assert PermissionMode.from_string("ask") == PermissionMode.ASK

    def test_from_string_invalid_raises(self):
        with pytest.raises(ValueError):
            PermissionMode.from_string("yolo")


class TestRegistry:
    def test_mutating_set_contents(self):
        assert "file_write" in MUTATING_TOOLS
        assert "shell_run" in MUTATING_TOOLS
        assert "file_read" not in MUTATING_TOOLS

    def test_safe_set_contents(self):
        assert "file_read" in SAFE_TOOLS
        assert "list_dir" in SAFE_TOOLS

    def test_sets_disjoint(self):
        assert not (MUTATING_TOOLS & SAFE_TOOLS)

    def test_is_mutating_by_name(self):
        assert PermissionRegistry.is_mutating("file_write") is True
        assert PermissionRegistry.is_mutating("file_read") is False
        assert PermissionRegistry.is_mutating("unknown_tool") is False

    def test_is_mutating_prefers_tool_property(self):
        class FakeTool:
            is_mutating = True

        assert PermissionRegistry.is_mutating("file_read", tool=FakeTool()) is True

    def test_get_blocked_tools(self):
        assert PermissionRegistry.get_blocked_tools() == set(MUTATING_TOOLS)


class TestChecker:
    def test_normal_allows_everything(self):
        checker = PermissionChecker(PermissionMode.NORMAL)
        result = checker.check("file_write")
        assert result.allowed is True

    def test_read_only_blocks_mutating(self):
        checker = PermissionChecker(PermissionMode.READ_ONLY)
        result = checker.check("file_write")
        assert result.allowed is False
        assert "read_only" in result.mode_applied

    def test_read_only_allows_safe(self):
        checker = PermissionChecker(PermissionMode.READ_ONLY)
        assert checker.check("file_read").allowed is True

    def test_ask_mode_needs_confirmation(self):
        async def ask(prompt, args):
            return True

        checker = PermissionChecker(PermissionMode.ASK, ask_user_callback=ask)
        result = checker.check("file_write")
        assert result.allowed is False
        assert result.needs_confirmation is True

    def test_mode_switch_clears_cache(self):
        checker = PermissionChecker(PermissionMode.READ_ONLY)
        checker.check("file_write")
        assert checker._cache
        checker.mode = PermissionMode.NORMAL
        assert checker._cache == {}
        assert checker.check("file_write").allowed is True

    def test_get_blocked_tools_per_mode(self):
        assert PermissionChecker(PermissionMode.NORMAL).get_blocked_tools() == set()
        assert PermissionChecker(PermissionMode.READ_ONLY).get_blocked_tools() == MUTATING_TOOLS

    def test_check_with_tool(self):
        class MutatingTool:
            name = "x"
            is_mutating = True

        checker = PermissionChecker(PermissionMode.READ_ONLY)
        assert checker.check_with_tool(MutatingTool()).allowed is False


class TestToolGate:
    async def test_lock_acquire_release(self):
        gate = ToolGate()
        assert gate.is_locked is False
        await gate.wait(holder_id="t1")
        assert gate.is_locked is True
        assert gate.holder == "t1"
        await gate.release()
        assert gate.is_locked is False
        assert gate.holder is None

    async def test_release_unheld_raises(self):
        gate = ToolGate()
        with pytest.raises(RuntimeError):
            await gate.release()

    async def test_mutual_exclusion(self):
        gate = ToolGate()
        order = []

        async def worker(name: str, hold: float):
            await gate.wait(holder_id=name)
            order.append(f"{name}-start")
            await asyncio.sleep(hold)
            order.append(f"{name}-end")
            await gate.release()

        await asyncio.gather(worker("a", 0.05), worker("b", 0.0))
        # b must not start before a finishes
        assert order == ["a-start", "a-end", "b-start", "b-end"]
