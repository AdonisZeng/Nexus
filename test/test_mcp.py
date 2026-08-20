"""MCP: qualified name parsing, server config validation, approval policies."""
import pytest

from src.mcp.client import MCPServerConfig, parse_qualified_tool_name
from src.mcp.approval import MCPToolApproval, ApprovalDecision


class TestQualifiedToolName:
    def test_parse_valid(self):
        server, tool = parse_qualified_tool_name("mcp__github__create_issue")
        assert server == "github"
        assert tool == "create_issue"

    def test_tool_name_with_double_underscore_kept(self):
        server, tool = parse_qualified_tool_name("mcp__fs__read__file")
        assert server == "fs"
        assert tool == "read__file"

    def test_missing_prefix_raises(self):
        with pytest.raises(ValueError):
            parse_qualified_tool_name("github__create_issue")

    def test_missing_tool_part_raises(self):
        with pytest.raises(ValueError):
            parse_qualified_tool_name("mcp__github")
        with pytest.raises(ValueError):
            parse_qualified_tool_name("mcp____tool")


class TestMCPServerConfig:
    def test_stdio_requires_command(self):
        with pytest.raises(ValueError, match="command"):
            MCPServerConfig(name="s", type="stdio")

    def test_http_requires_url(self):
        with pytest.raises(ValueError, match="url"):
            MCPServerConfig(name="s", type="http")

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            MCPServerConfig(name="s", type="grpc")

    def test_valid_stdio(self):
        cfg = MCPServerConfig(name="fs", type="stdio", command=["npx", "x"])
        assert cfg.enabled is True
        assert cfg.env == {}

    def test_valid_http(self):
        cfg = MCPServerConfig(name="gh", type="http", url="https://example.local/mcp")
        assert cfg.url == "https://example.local/mcp"


class TestApprovalDefaultPolicy:
    def test_dangerous_verbs_prompt(self):
        approval = MCPToolApproval()
        for verb in ("delete_repo", "remove_file", "destroy_all", "drop_table", "truncate_data"):
            assert approval.get_default_policy("s", verb) == ApprovalDecision.PROMPT

    def test_write_verbs_prompt(self):
        approval = MCPToolApproval()
        for verb in ("create_issue", "write_file", "update_pr", "edit_doc", "modify_config"):
            assert approval.get_default_policy("s", verb) == ApprovalDecision.PROMPT

    def test_read_verbs_approve(self):
        approval = MCPToolApproval()
        assert approval.get_default_policy("s", "read_file") == ApprovalDecision.APPROVE
        assert approval.get_default_policy("s", "list_issues") == ApprovalDecision.APPROVE

    def test_other_verbs_approve(self):
        approval = MCPToolApproval()
        assert approval.get_default_policy("s", "search_code") == ApprovalDecision.APPROVE


class TestApprovalRules:
    async def test_configured_rule_overrides_default(self):
        approval = MCPToolApproval({"github": {"create_issue": "approve"}})
        decision = await approval.check("github", "create_issue")
        assert decision == ApprovalDecision.APPROVE
        # unconfigured tool falls back to default policy
        decision = await approval.check("github", "delete_repo")
        assert decision == ApprovalDecision.PROMPT

    async def test_load_from_config(self):
        approval = MCPToolApproval()
        approval.load_from_config({
            "approvals": {"fs": {"write_file": "deny"}},
        })
        decision = await approval.check("fs", "write_file")
        assert decision == ApprovalDecision.DENY

    def test_set_and_get_policy(self):
        approval = MCPToolApproval()
        assert approval.get_policy("fs", "read_file") is None
        approval.set_policy("fs", "read_file", ApprovalDecision.PROMPT)
        assert approval.get_policy("fs", "read_file") == ApprovalDecision.PROMPT
        assert approval.get_all_policies() == {"fs": {"read_file": "prompt"}}

    def test_load_from_config_ignores_invalid_entries(self):
        approval = MCPToolApproval()
        approval.load_from_config({"approvals": {"broken": "not-a-dict"}})
        assert approval.get_all_policies() == {"broken": {}}
