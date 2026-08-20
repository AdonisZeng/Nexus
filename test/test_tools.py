"""Tool registry completeness, schema generation, and real tool execution."""
import pytest

from src.tools.registry import ToolRegistry, Tool

EXPECTED_TOOLS = {
    "file_read", "file_write", "file_patch", "file_search",
    "list_dir", "shell_run", "code_exec",
    "subagent", "check_subagent", "cancel_subagent",
    "background_run", "check_background", "todo",
}


@pytest.fixture(scope="module")
def registry():
    return ToolRegistry()


class TestRegistry:
    def test_builtin_tools_registered(self, registry):
        names = set(registry.list_tools())
        missing = EXPECTED_TOOLS - names
        assert not missing, f"missing tools: {missing} (registered: {sorted(names)})"

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("nope") is None

    def test_schemas_have_required_keys(self, registry):
        schemas = registry.get_tools_schema()
        assert len(schemas) == len(registry.list_tools())
        for schema in schemas:
            assert "name" in schema and schema["name"]
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"].get("type") == "object"

    def test_every_tool_is_tool_instance(self, registry):
        for name in registry.list_tools():
            assert isinstance(registry.get(name), Tool)

    async def test_execute_unknown_raises(self, registry):
        with pytest.raises(ValueError):
            await registry.execute("nope")


class TestRealExecution:
    async def test_file_write_and_read(self, registry, tmp_path):
        await registry.execute(
            "file_write",
            file_path="hello.txt",
            content="hello world",
            cwd=str(tmp_path),
        )
        result = await registry.execute(
            "file_read", file_path="hello.txt", cwd=str(tmp_path)
        )
        assert "hello world" in result

    async def test_file_read_missing_raises(self, registry, tmp_path):
        with pytest.raises(FileNotFoundError):
            await registry.execute(
                "file_read", file_path="missing.txt", cwd=str(tmp_path)
            )

    async def test_list_dir(self, registry, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        result = str(await registry.execute("list_dir", dir_path=str(tmp_path)))
        assert "a.txt" in result

    async def test_todo_tool(self, registry):
        result = await registry.execute("todo", items=[
            {"id": "1", "text": "step one", "status": "in_progress"},
        ])
        assert "[>] #1: step one" in result

    async def test_todo_tool_validation_error(self, registry):
        with pytest.raises(ValueError):
            await registry.execute("todo", items=[
                {"id": "1", "text": "a", "status": "in_progress"},
                {"id": "2", "text": "b", "status": "in_progress"},
            ])
