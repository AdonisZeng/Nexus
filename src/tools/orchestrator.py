"""Tool orchestrator for managing tool execution lifecycle."""
from typing import Any, Optional, TYPE_CHECKING

from .context import ToolContext, ToolGate
from .registry import Tool

if TYPE_CHECKING:
    from src.hooks import HookRunner


class ToolOrchestrator:
    """Orchestrates tool execution with proper lifecycle management.

    The orchestrator manages the complete execution flow of tools,
    including pre-execution hooks, gate management for mutating operations,
    and post-execution cleanup.

    Example:
        gate = ToolGate()
        orchestrator = ToolOrchestrator(gate)
        result = await orchestrator.execute(tool, args, context)
    """

    def __init__(self, gate: ToolGate, hook_runner: Optional["HookRunner"] = None):
        """Initialize the orchestrator with a gate.

        Args:
            gate: The gate used to control mutating operations
            hook_runner: Optional HookRunner for pre/post tool hooks
        """
        self._gate = gate
        self._hook_runner = hook_runner

    async def execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        context: Optional[ToolContext] = None
    ) -> Any:
        """Execute a tool with full lifecycle management.

        This method orchestrates the complete tool execution flow:
        0. Run tool_call_start hooks (before gate acquisition)
        1. Calls tool.before_execute() for pre-execution setup
        2. If tool.is_mutating, acquires the gate lock via context.gate.wait()
        3. Calls tool.execute() to perform the actual operation
        4. Calls tool.after_execute(result) for post-execution cleanup
        5. Run tool_call_end hooks
        6. Releases gate lock if acquired (in finally block)

        Args:
            tool: The tool instance to execute
            args: Arguments to pass to the tool
            context: Optional execution context. If not provided, a default
                     context will be created with the gate attached.

        Returns:
            The result of the tool execution

        Raises:
            Exception: Any exception raised during tool execution is propagated
                      after proper cleanup (gate release, after_execute)
        """
        # Create context if not provided
        if context is None:
            context = ToolContext(
                tool_name=tool.name,
                args=args,
                gate=self._gate
            )
        elif context.gate is None:
            context = context.with_gate(self._gate)

        result: Any = None
        gate_acquired = False

        try:
            # Step 0: Run tool_call_start hooks (before gate acquisition)
            if self._hook_runner:
                hook_result = await self._hook_runner.run_pre_tool(tool.name, args)
                if hook_result.blocked:
                    raise PermissionError(f"Tool '{tool.name}' blocked by hook")
                if hook_result.updated_input:
                    args = hook_result.updated_input

            # Step 1: Pre-execution hook
            if hasattr(tool, "before_execute"):
                await tool.before_execute(context=context)

            # Step 2: Check if tool is mutating and acquire gate if needed
            is_mutating = getattr(tool, "is_mutating", False)
            if is_mutating and context.gate is not None:
                await context.gate.wait(holder_id=tool.name)
                gate_acquired = True

            # Step 3: Execute the tool
            result = await tool.execute(**args)

            # Step 4: Post-execution hook
            if hasattr(tool, "after_execute"):
                await tool.after_execute(result, context=context)

            # Step 5: Run tool_call_end hooks
            if self._hook_runner:
                result_str = str(result) if result is not None else ""
                hook_result = await self._hook_runner.run_post_tool(
                    tool.name, args, result_str
                )
                # Hook messages are not used here since we already have the result

        finally:
            # Gate release (runs after hooks complete)
            if gate_acquired and context.gate is not None:
                await context.gate.release()

        return result

    @property
    def gate(self) -> ToolGate:
        """Get the gate associated with this orchestrator.

        Returns:
            The ToolGate instance used by this orchestrator
        """
        return self._gate
