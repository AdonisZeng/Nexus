"""Tool registry and base tool"""
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, Optional, Type, TYPE_CHECKING
import asyncio
import subprocess
from pathlib import Path

from pydantic import BaseModel

from .schema_cleaner import SchemaCleaner

if TYPE_CHECKING:
    from src.adapters.provider import ModelProvider


class ModelProviderMixin:
    """Mixin for tools that need access to the model adapter via injection."""

    _provider: Optional["ModelProvider"] = None

    def _get_adapter(self):
        """Get model adapter from injected provider, or None if not available."""
        if self._provider is not None:
            return self._provider.get_adapter()
        return None


class Tool(ABC):
    """Base class for tools"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description"""
        pass

    @property
    def is_mutating(self) -> bool:
        """Whether this tool modifies state (files, data, etc.)"""
        return False

    @property
    def requires_approval(self) -> bool:
        """Whether this tool requires user approval before execution"""
        return False

    @property
    def is_concurrent_safe(self) -> bool:
        """Whether this tool can safely execute in parallel with other read tools.

        Read-only tools (is_mutating=False) are generally concurrent safe.
        Mutating tools may have internal state that prevents parallel execution.
        """
        return not self.is_mutating

    @property
    def concurrency_category(self) -> str:
        """Category for dependency analysis: 'read', 'write', or 'other'.

        Used by DependencyAnalyzer to group tools for parallel execution.
        - 'read': Safe to run in parallel with other read tools
        - 'write': Must be serialized with other write/other tools
        - 'other': Unknown classification, treated as write for safety
        """
        if self.is_mutating:
            return "write"
        return "read"

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool"""
        pass

    async def before_execute(self, **kwargs) -> Optional[str]:
        """
        Hook called before execute.

        @return None to continue execution, or error message string to abort
        """
        return None

    async def after_execute(self, result: Any, **kwargs):
        """
        Hook called after execute.

        @param result The result from execute()
        """
        pass

    def get_schema(self, input_model: Optional[Type[BaseModel]] = None) -> dict:
        """
        Get tool schema for model.

        @param input_model Optional Pydantic model to generate input schema from
        @return Tool schema dictionary
        """
        schema = {
            "name": self.name,
            "description": self.description,
            "input_schema": self._get_input_schema()
        }

        if input_model is not None:
            schema["input_schema"] = input_model.model_json_schema()

        return schema

    def _get_input_schema(self) -> dict:
        """Get input schema - override in subclass"""
        return {
            "type": "object",
            "properties": {},
            "required": []
        }


class ToolRegistry:
    """Registry for all available tools"""

    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self):
        """Register built-in tools"""
        from .file import FileReadTool, FileWriteTool, FileSearchTool
        from .shell import ShellTool
        from .code import CodeExecTool
        from .patch import FilePatchTool
        from .list_dir import ListDirTool
        from .subagent import SubagentTool, CheckSubagentTool, CancelSubagentTool
        from .background_tools import BackgroundRunTool, CheckBackgroundTool
        from .todo import TodoTool

        self.register(FileReadTool())
        self.register(FileWriteTool())
        self.register(FilePatchTool())
        self.register(FileSearchTool())
        self.register(ListDirTool())
        self.register(ShellTool())
        self.register(CodeExecTool())
        self.register(SubagentTool())
        self.register(CheckSubagentTool())
        self.register(CancelSubagentTool())
        self.register(BackgroundRunTool())
        self.register(CheckBackgroundTool())
        self.register(TodoTool())

    def register(self, tool):
        """Register a tool"""
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Get a tool by name"""
        return self.tools.get(name)

    def get_tools_schema(
        self,
        provider: str = None,
        tool_schema_profile: str = None
    ) -> list[dict]:
        """
        Get all tool schemas, optionally cleaned for specific providers.

        Args:
            provider: Provider name (e.g., "google", "gemini", "xai")
            tool_schema_profile: Specific profile to use for schema cleaning

        Returns:
            List of tool schemas
        """
        schemas = [tool.get_schema() for tool in self.tools.values()]

        if not schemas or (not provider and not tool_schema_profile):
            return schemas

        # Apply schema cleaning if needed
        if provider or tool_schema_profile:
            return self._clean_schemas(schemas, provider, tool_schema_profile)

        return schemas

    def _clean_schemas(
        self,
        schemas: list[dict],
        provider: str,
        tool_schema_profile: str = None
    ) -> list[dict]:
        """Clean schemas for specific provider"""
        cleaned = []
        for schema in schemas:
            input_schema = schema.get("input_schema", {})
            cleaned_schema = SchemaCleaner.clean_for_provider(
                input_schema,
                provider,
                tool_schema_profile
            )
            cleaned.append({
                **schema,
                "input_schema": cleaned_schema
            })
        return cleaned

    def list_tools(self) -> list[str]:
        """List all tool names"""
        return list(self.tools.keys())

    async def execute(self, tool_name: str, **kwargs) -> Any:
        """Execute a tool by name"""
        tool = self.get(tool_name)
        if not tool:
            raise ValueError(f"Unknown tool: {tool_name}")
        return await tool.execute(**kwargs)


# Global registry instance
global_registry = ToolRegistry()