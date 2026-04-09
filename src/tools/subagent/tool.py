"""Subagent tool - allows main agent to invoke subagents"""
import asyncio
from typing import TYPE_CHECKING, Any, Optional

from src.tools.registry import Tool
from src.tools.subagent.registry import SubagentRegistry
from src.tools.subagent.runner import SubagentRunner
from src.tools.subagent.models import SubagentResult
from src.utils import get_logger, get_output_sink

if TYPE_CHECKING:
    from src.adapters.provider import ModelProvider

logger = get_logger("subagent.tool")


class SubagentTool(Tool):
    """Tool for invoking subagents - registered as 'subagent' in ToolRegistry"""

    is_mutating = False
    requires_approval = False

    def __init__(
        self,
        registry: Optional[SubagentRegistry] = None,
        provider: Optional["ModelProvider"] = None
    ):
        self._registry = registry or SubagentRegistry()
        self._provider = provider  # ModelProvider for dependency injection
        self._loaded = False

    def _ensure_loaded(self):
        """Lazy load agents only when first needed"""
        if not self._loaded:
            self._registry.load_agents()
            self._loaded = True

    @property
    def name(self) -> str:
        self._ensure_loaded()
        return "subagent"

    @property
    def description(self) -> str:
        self._ensure_loaded()
        agents = self._registry.list_agents()
        if not agents:
            return (
                "调用子代理执行独立任务。当前无可用的子代理。"
                "参数: prompt (string, 必填) - 给子代理的完整指令"
            )

        # Build agent list for LLM to see
        agent_lines = []
        for name in agents:
            config = self._registry.get(name)
            if config:
                agent_lines.append(f"- **{name}**: {config.description}")

        agent_list = "\n".join(agent_lines) if agent_lines else "无可用子代理"

        return (
            f"""调用子代理执行独立任务。LLM应根据用户问题判断是否需要调用子代理，并选择合适的子代理。

可用子代理列表：
{agent_list}

参数:
- prompt (string, 必填): 给子代理的完整任务指令，包含任务目标、所需上下文和输出格式要求。LLM应根据可用代理和用户问题构造合适的prompt。
- agent (string, 可选): 子代理名称。建议明确指定以确保调用正确的代理。"""
        )

    async def execute(
        self,
        prompt: str,
        agent: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        """
        Execute a subagent with the given prompt.

        Args:
            prompt: The task instruction for the subagent
            agent: Optional subagent name. If not provided, auto-routes based on prompt

        Returns:
            The subagent's final output message
        """
        self._ensure_loaded()

        # Validate prompt
        if not prompt or not prompt.strip():
            return "Error: prompt is required"

        prompt = prompt.strip()

        # Check for parallel execution hint
        if "parallel:" in prompt[:50].lower():
            return await self._execute_parallel(prompt, agent)

        # Get subagent config
        config = self._resolve_agent(prompt, agent)
        if not config:
            available = ", ".join(self._registry.list_agents()) if self._registry.agents else "none"
            return f"Error: No suitable subagent found. Available: [{available}]"

        # Get adapter and tool registry
        adapter = self._get_adapter()
        if not adapter:
            return "Error: No model adapter available"

        tool_registry = self._get_tool_registry()

        # Show UI feedback
        sink = get_output_sink()
        sink.print(f"[dim]正在调用子代理 [{config.name}]...[/dim]")

        # Run subagent
        runner = SubagentRunner(
            config=config,
            adapter=adapter,
            tool_registry=tool_registry,
        )

        result = await runner.run(prompt)

        sink.print(f"[dim]子代理 [{config.name}] 执行完成[/dim]")

        return self._format_result(config.name, result)

    def _resolve_agent(
        self,
        prompt: str,
        explicit_agent: Optional[str]
    ) -> Optional[Any]:
        """Resolve which subagent to use"""
        self._ensure_loaded()
        # Explicit agent name
        if explicit_agent:
            config = self._registry.get(explicit_agent)
            if not config:
                logger.warning(f"Subagent '{explicit_agent}' not found")
            return config

        # Auto-route by description
        config = self._registry.find_by_description(prompt)
        if config:
            logger.debug(f"Auto-routed to subagent: {config.name}")
        else:
            logger.debug("No matching subagent found for prompt")

        return config

    async def _execute_parallel(
        self,
        prompt: str,
        default_agent: Optional[str]
    ) -> str:
        """Execute multiple subagents in truly parallel using asyncio.gather"""
        self._ensure_loaded()

        # Parse parallel tasks from prompt
        # Format: "parallel:\n- task 1\n- task 2\n- task 3"
        lines = prompt.split("\n")
        tasks = [line.strip() for line in lines[1:] if line.strip()]

        if not tasks:
            return "Error: parallel prompt format invalid"

        sink = get_output_sink()
        sink.print(f"[dim]正在并行调用 {len(tasks)} 个子代理...[/dim]")

        async def run_task(task: str) -> str:
            """Run a single task in the subagent"""
            config = self._resolve_agent(task, default_agent)
            if not config:
                return f"[Skipped: no agent for '{task[:30]}...']"

            adapter = self._get_adapter()
            tool_registry = self._get_tool_registry()
            runner = SubagentRunner(config=config, adapter=adapter, tool_registry=tool_registry)
            result = await runner.run(task)
            return self._format_result(config.name, result)

        # Execute all tasks in parallel using asyncio.gather
        results = await asyncio.gather(
            *[run_task(task) for task in tasks],
            return_exceptions=True
        )

        # Handle exceptions
        formatted_results = []
        for result in results:
            if isinstance(result, Exception):
                formatted_results.append(f"[Error: {str(result)}]")
            else:
                formatted_results.append(result)

        sink.print(f"[dim]并行子代理调用完成[/dim]")

        return "\n\n".join(formatted_results)

    def _format_result(self, agent_name: str, result: SubagentResult) -> str:
        """Format subagent result for display"""
        if result.success:
            output = result.output.strip() if result.output else "[No output]"
            return f"[{agent_name}] {output}"
        else:
            error = result.error or "Unknown error"
            return f"[{agent_name}] Error: {error}"

    def _get_adapter(self):
        """Get the current model adapter.

        Uses injected provider if available, otherwise falls back to
        global adapter for backward compatibility.
        """
        # First try injected provider (preferred)
        if self._provider is not None:
            adapter = self._provider.get_adapter()
            if adapter is not None:
                return adapter

        # Fall back to global adapter (backward compatibility)
        try:
            from src.adapters import get_current_adapter
            return get_current_adapter()
        except ImportError:
            logger.error("Could not import get_current_adapter")
            return None

    def _get_tool_registry(self):
        """Get the global tool registry"""
        try:
            from src.tools.registry import global_registry
            return global_registry
        except ImportError:
            logger.error("Could not import global_registry")
            return None

    def _get_input_schema(self) -> dict:
        """Return the input schema for this tool"""
        self._ensure_loaded()
        # Get available agents for schema documentation
        agents = self._registry.list_agents()
        agent_names = ", ".join(agents) if agents else "无可用代理"

        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "给子代理的完整任务指令，包含任务目标、所需上下文和输出格式要求。LLM应根据可用代理和用户问题构造合适的prompt。可用代理: " + agent_names
                },
                "agent": {
                    "type": "string",
                    "description": f"子代理名称（可选）。可用代理: {agent_names}。建议明确指定以确保调用正确的代理。"
                }
            },
            "required": ["prompt"]
        }


__all__ = ["SubagentTool"]
