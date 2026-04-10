"""Async HookRunner wrapper for use in components."""
import asyncio
from pathlib import Path
from typing import Any, Optional

from .manager import HookManager
from .models import HookEvent, HookResult


class HookRunner:
    """
    Async wrapper around HookManager for use in async components.

    Provides async methods that run hooks via run_in_executor
    to avoid blocking the event loop.
    """

    def __init__(
        self,
        manager: HookManager,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[Path] = None,
        adapter=None,
        system_prompt: str = "",
        inherited_messages: Optional[list] = None,
    ):
        """
        Initialize HookRunner.

        Args:
            manager: HookManager instance
            env: Additional env vars for hook execution
            cwd: Working directory for hook execution
            adapter: Model adapter for agent hooks
            system_prompt: System prompt for agent hooks
            inherited_messages: Messages to inherit for agent hooks
        """
        self._manager = manager
        self._env = env or {}
        self._cwd = cwd
        self._adapter = adapter
        self._system_prompt = system_prompt
        self._inherited_messages = inherited_messages or []

        # Pass agent context to manager
        self._manager.set_agent_context(adapter, system_prompt, inherited_messages or [])

    @property
    def is_enabled(self) -> bool:
        """Whether hooks are enabled (workspace trusted)."""
        return self._manager._check_workspace_trust()

    async def run_agent_start(
        self,
        agent_id: str = "",
        iteration: int = 1
    ) -> HookResult:
        """Run agent_start hooks."""
        context = {"agent_id": agent_id, "iteration": iteration}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.AGENT_START,
            context,
            self._cwd,
        )

    async def run_agent_end(
        self,
        agent_id: str = "",
        reason: str = ""
    ) -> HookResult:
        """Run agent_end hooks."""
        context = {"agent_id": agent_id, "reason": reason}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.AGENT_END,
            context,
            self._cwd,
        )

    async def run_iteration_start(
        self,
        iteration: int = 1,
        agent_id: str = ""
    ) -> HookResult:
        """Run iteration_start hooks."""
        context = {"iteration": iteration, "agent_id": agent_id}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.ITERATION_START,
            context,
            self._cwd,
        )

    async def run_iteration_end(
        self,
        iteration: int = 1,
        agent_id: str = ""
    ) -> HookResult:
        """Run iteration_end hooks."""
        context = {"iteration": iteration, "agent_id": agent_id}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.ITERATION_END,
            context,
            self._cwd,
        )

    async def run_pre_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        agent_id: str = ""
    ) -> HookResult:
        """Run tool_call_start hooks."""
        context = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "agent_id": agent_id,
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.TOOL_CALL_START,
            context,
            self._cwd,
        )

    async def run_post_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str,
        agent_id: str = ""
    ) -> HookResult:
        """Run tool_call_end hooks."""
        context = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "agent_id": agent_id,
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.TOOL_CALL_END,
            context,
            self._cwd,
        )

    async def run_tool_blocked(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        reason: str,
        agent_id: str = ""
    ) -> HookResult:
        """Run tool_blocked hooks."""
        context = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "reason": reason,
            "agent_id": agent_id,
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.TOOL_BLOCKED,
            context,
            self._cwd,
        )

    async def run_context_compressed(
        self,
        agent_id: str = "",
        reason: str = ""
    ) -> HookResult:
        """Run context_compressed hooks."""
        context = {"agent_id": agent_id, "reason": reason}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._manager.run_hooks,
            HookEvent.CONTEXT_COMPRESSED,
            context,
            self._cwd,
        )

    async def run_terminated(
        self,
        reason: str = ""
    ) -> HookResult:
        """Run terminated hooks (maps to agent_end for backward compatibility)."""
        return await self.run_agent_end(reason=reason)


__all__ = ["HookRunner"]
