"""Agent configuration utilities for /agents command"""
import json
import re
from pathlib import Path
from typing import Optional

from src.tools.subagent import SubagentConfig, SubagentRegistry
from src.tools import global_registry
from src.adapters import get_current_adapter


AGENTS_DIR = Path.home() / ".nexus" / "agents"


class AgentConfigEditor:
    """Agent configuration editor"""

    @staticmethod
    def load_all_agents() -> list[SubagentConfig]:
        """Load all agent configurations"""
        registry = SubagentRegistry()
        registry.load_agents()
        return list(registry.agents.values())

    @staticmethod
    def save_agent(config: SubagentConfig) -> None:
        """Save agent configuration to ~/.nexus/agents/{name}.md"""
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)

        file_path = AGENTS_DIR / f"{config.name}.md"

        # Build frontmatter
        frontmatter = {
            "name": config.name,
            "description": config.description,
        }

        if config.allowed_tools:
            frontmatter["allowed-tools"] = config.allowed_tools

        if config.denied_tools:
            frontmatter["denied-tools"] = config.denied_tools

        if config.model:
            frontmatter["model"] = config.model

        if config.max_iterations != 10:
            frontmatter["max-iterations"] = config.max_iterations

        if config.timeout_seconds != 300.0:
            frontmatter["timeout-seconds"] = config.timeout_seconds

        # Write file
        import yaml
        content = "---\n"
        content += yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
        content += "---\n"
        content += config.system_prompt.strip() + "\n"

        file_path.write_text(content, encoding="utf-8")

    @staticmethod
    def delete_agent(name: str) -> bool:
        """Delete an agent configuration file. Returns True if deleted."""
        file_path = AGENTS_DIR / f"{name}.md"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    @staticmethod
    def get_available_tools() -> list[str]:
        """Get list of available tools from global registry (excluding subagent)"""
        return [t for t in global_registry.list_tools() if t != "subagent"]

    @staticmethod
    def get_main_agent_tools() -> list[str]:
        """Get main agent's allowed tools (all tools except subagent)"""
        tools = global_registry.list_tools()
        return [t for t in tools if t != "subagent"]

    @staticmethod
    async def auto_generate_agent(
        raw_description: str,
        inherited_tools: Optional[list[str]] = None
    ) -> SubagentConfig:
        """
        Auto-generate agent configuration from user description.
        Calls LLM to generate name, description, and system_prompt.
        """
        adapter = get_current_adapter()
        if not adapter:
            raise RuntimeError("No model adapter available")

        prompt = f"""根据用户对子代理的描述，生成规范化的配置：

用户描述：{raw_description}

要求：
- name：简短英文名称，只用字母和连字符（如 code-reviewer）
- description：一句话描述，用中文撰写，清晰说明何时应该调用该子代理
- system_prompt：详细的工作规范和职责说明，包含工作方式、输出格式等，供主Agent在决定调用时使用

请严格按照以下JSON格式返回，不要添加任何其他内容：
{{"name": "...", "description": "...", "system_prompt": "..."}}"""

        response = await adapter.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=None
        )

        # Parse JSON response
        try:
            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)

            config = SubagentConfig(
                name=data["name"],
                description=data["description"],
                system_prompt=data["system_prompt"],
                allowed_tools=inherited_tools or [],
            )
            return config

        except (json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(f"Failed to parse LLM response: {e}\nResponse: {response}")

    @staticmethod
    def build_agent_from_input(
        name: str,
        description: str,
        system_prompt: str,
        allowed_tools: list[str],
        model: Optional[str] = None,
        max_iterations: int = 10,
        timeout_seconds: float = 300.0,
    ) -> SubagentConfig:
        """Build agent config from user input"""
        return SubagentConfig(
            name=name,
            description=description,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model=model,
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
        )


__all__ = ["AgentConfigEditor", "AGENTS_DIR"]
