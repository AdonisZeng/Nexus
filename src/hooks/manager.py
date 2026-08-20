"""Global HookManager with subprocess execution and exit code contract."""
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from src.utils import get_logger

from .config import _HOOKS_CONFIG_FILE, load_hooks_config, get_hooks_for_event
from .models import HookDefinition, HookEvent, HookResult

logger = get_logger("hooks.manager")

# Exit code contract:
# 0: continue execution
# 1: block operation
# 2: inject message into context
HOOK_EXIT_CONTINUE = 0
HOOK_EXIT_BLOCK = 1
HOOK_EXIT_INJECT = 2

# Hook timeout in seconds
HOOK_TIMEOUT = 30


class HookManager:
    """
    Global hook manager for all components.

    Supports:
    - Loading hooks from ~/.nexus/hooks.json
    - Per-component local hooks
    - Subprocess hooks (type="subprocess")
    - Agent hooks (type="agent") that spawn SubagentRunner
    - Exit code contract: 0=continue, 1=block, 2=inject message
    - Structured stdout parsing for updatedInput, additionalContext, permissionDecision
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        local_hooks: Optional[dict[str, list[HookDefinition]]] = None,
    ):
        """
        Initialize HookManager.

        Args:
            config_path: Path to global hooks.json. Defaults to ~/.nexus/hooks.json
            local_hooks: Optional dict of event -> list of HookDefinition for local hooks
        """
        self._config_path = config_path or _HOOKS_CONFIG_FILE
        self._local_hooks: dict[str, list[HookDefinition]] = local_hooks or {}
        self._global_config: Optional[dict] = None
        # Agent context for agent hooks
        self._adapter = None
        self._system_prompt = ""
        self._inherited_messages: list = []

    def set_agent_context(
        self,
        adapter=None,
        system_prompt: str = "",
        inherited_messages: Optional[list] = None,
    ) -> None:
        """Set context for agent hooks."""
        self._adapter = adapter
        self._system_prompt = system_prompt
        self._inherited_messages = inherited_messages or []

    def load_global_hooks(self) -> None:
        """Load global hooks from config file."""
        self._global_config = load_hooks_config(self._config_path)

    @property
    def trust_all(self) -> bool:
        """Check if trust_all is enabled."""
        if self._global_config is None:
            self.load_global_hooks()
        return self._global_config.get("trust_all", False) is True

    def _check_workspace_trust(self) -> bool:
        """Check if workspace is trusted."""
        if os.environ.get("NEXUS_SDK_MODE"):
            return True
        if self.trust_all:
            return True
        return _HOOKS_CONFIG_FILE.exists()

    def _format_agent_prompt(
        self,
        prompt_template: str,
        context: Optional[dict[str, Any]] = None
    ) -> str:
        """Format agent hook prompt with context variables."""
        if not prompt_template:
            return ""

        # Available placeholders: {tool_name}, {tool_input}, {tool_output}, {event}
        replacements = {
            "tool_name": context.get("tool_name", "") if context else "",
            "tool_input": json.dumps(context.get("tool_input", {}), ensure_ascii=False) if context else "{}",
            "tool_output": str(context.get("tool_output", "")) if context else "",
            "event": context.get("event", "") if context else "",
            "iteration": str(context.get("iteration", "")) if context else "",
        }

        result = prompt_template
        for key, value in replacements.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    def _run_agent_hook_sync(
        self,
        hook_def: HookDefinition,
        context: Optional[dict[str, Any]] = None
    ) -> Optional[list]:
        """
        Run an agent hook synchronously using SubagentRunner.

        Args:
            hook_def: Hook definition with type="agent"
            context: Context dict

        Returns:
            List of messages from agent result, or None if failed
        """
        if not hook_def.agent_prompt or not self._adapter:
            logger.warning("[HookManager] Agent hook missing prompt or adapter")
            return None

        logger.info(f"[HookManager] Running agent hook: {hook_def.id or 'anonymous'}")

        try:
            from src.tools.subagent import SubagentConfig, SubagentRunner
            from src.tools.registry import global_registry

            # Format prompt with context
            prompt = self._format_agent_prompt(hook_def.agent_prompt, context)

            # Create config for agent hook
            config = SubagentConfig(
                name=f"hook_agent_{hook_def.id or 'anonymous'}",
                description="Agent hook",
                system_prompt=self._system_prompt,
                max_iterations=hook_def.agent_max_iterations,
                permission_mode="read_only",  # Safe default
            )

            # Create runner
            runner = SubagentRunner(
                config=config,
                adapter=self._adapter,
                tool_registry=global_registry,
            )

            # Run synchronously using asyncio.run()
            # Since we're in a sync context (called from run_in_executor), use asyncio.run()
            import asyncio
            result = asyncio.run(
                runner.run_with_inherited_context(prompt, self._inherited_messages)
            )

            if result.success:
                logger.info(f"[HookManager] Agent hook completed: {result.output[:100]}...")
                return [result.output] if result.output else []
            else:
                logger.warning(f"[HookManager] Agent hook failed: {result.error}")
                return [f"[Agent hook error: {result.error}]"] if result.error else []

        except Exception as e:
            logger.error(f"[HookManager] Agent hook exception: {e}")
            return [f"[Agent hook exception: {str(e)}]"]

    def _build_env(
        self,
        event: str,
        context: Optional[dict[str, Any]] = None
    ) -> dict[str, str]:
        """Build environment variables for hook execution."""
        env = dict(os.environ)
        env["HOOK_EVENT"] = event

        if context:
            env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
            tool_input = context.get("tool_input", {})
            env["HOOK_TOOL_INPUT"] = json.dumps(tool_input, ensure_ascii=False)[:10000]
            if "tool_output" in context:
                env["HOOK_TOOL_OUTPUT"] = str(context["tool_output"])[:10000]
            if "iteration" in context:
                env["HOOK_ITERATION"] = str(context["iteration"])
            if "agent_id" in context:
                env["HOOK_AGENT_ID"] = str(context["agent_id"])

        return env

    def _run_single_hook(
        self,
        command: str,
        env: dict[str, str],
        cwd: Optional[Path] = None
    ) -> tuple[int, str, str]:
        """Execute a single hook command."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd) if cwd else None,
                env=env,
                capture_output=True,
                text=True,
                timeout=HOOK_TIMEOUT,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.warning(f"[HookManager] Hook timeout ({HOOK_TIMEOUT}s): {command[:50]}...")
            return -1, "", f"Timeout ({HOOK_TIMEOUT}s)"
        except Exception as e:
            logger.error(f"[HookManager] Hook error: {e}")
            return -1, "", str(e)

    def run_hooks(
        self,
        event: HookEvent,
        context: Optional[dict[str, Any]] = None,
        cwd: Optional[Path] = None,
    ) -> HookResult:
        """
        Run all hooks for an event.

        Args:
            event: HookEvent enum value
            context: Context dict with tool_name, tool_input, etc.
            cwd: Working directory for hook execution

        Returns:
            HookResult with blocked, messages, updated_input
        """
        result = HookResult()

        # Trust check
        if not self._check_workspace_trust():
            logger.debug("[HookManager] Workspace not trusted, skipping hooks")
            return result

        # Load global config if not already loaded
        if self._global_config is None:
            self.load_global_hooks()

        # Get hooks from both global and local sources
        event_name = event.value

        global_hooks = get_hooks_for_event(self._global_config or {}, event_name)
        local_hooks = self._local_hooks.get(event_name, [])

        all_hooks = global_hooks + local_hooks

        if not all_hooks:
            return result

        tool_name = context.get("tool_name", "") if context else ""
        env = self._build_env(event_name, context)

        for hook_def in all_hooks:
            # Matcher filter
            if hook_def.matcher and hook_def.matcher != "*":
                if hook_def.matcher != tool_name:
                    continue

            if hook_def.type == "agent":
                # Agent hook - run via SubagentRunner
                agent_result = self._run_agent_hook_sync(hook_def, context)
                if agent_result:
                    result.messages.extend(agent_result)
                continue

            # Subprocess hook
            if not hook_def.command:
                continue

            logger.debug(
                f"[HookManager] Running hook for {event_name}: {hook_def.command[:50]}..."
            )
            returncode, stdout, stderr = self._run_single_hook(
                hook_def.command, env, cwd
            )

            if returncode == HOOK_EXIT_CONTINUE:
                # Continue execution
                if stdout.strip():
                    logger.debug(
                        f"[HookManager] hook stdout: {stdout.strip()[:100]}"
                    )
                # Parse structured stdout
                try:
                    hook_output = json.loads(stdout)
                    if "updatedInput" in hook_output and context:
                        result.updated_input = hook_output["updatedInput"]
                    if "additionalContext" in hook_output:
                        msgs = hook_output["additionalContext"]
                        result.messages.extend(msgs if isinstance(msgs, list) else [msgs])
                    if "permissionDecision" in hook_output:
                        decision = hook_output["permissionDecision"]
                        result.permission_override = True if decision else False
                except (json.JSONDecodeError, TypeError):
                    pass

            elif returncode == HOOK_EXIT_BLOCK:
                # Block operation
                result.blocked = True
                reason = stderr.strip() or "Blocked by hook"
                logger.warning(
                    f"[HookManager] Hook blocked: {reason[:200]}"
                )

            elif returncode == HOOK_EXIT_INJECT:
                # Inject message
                msg = stderr.strip() if stderr.strip() else stdout.strip()
                if msg:
                    result.messages.append(msg)
                    logger.debug(
                        f"[HookManager] Hook injected message: {msg[:200]}"
                    )

            else:
                # Error (returncode == -1 or other)
                error_msg = (
                    stderr.strip() or stdout.strip() or f"Hook failed with code {returncode}"
                )
                logger.warning(f"[HookManager] Hook error: {error_msg[:200]}")

        return result

    def register_local_hook(
        self,
        event: HookEvent,
        hook: HookDefinition
    ) -> None:
        """Register a local hook for an event."""
        event_name = event.value
        if event_name not in self._local_hooks:
            self._local_hooks[event_name] = []
        self._local_hooks[event_name].append(hook)

    def clear_local_hooks(self, event: Optional[HookEvent] = None) -> None:
        """Clear local hooks, optionally for a specific event only."""
        if event is None:
            self._local_hooks.clear()
        else:
            self._local_hooks.pop(event.value, None)


__all__ = ["HookManager", "HookEvent"]
