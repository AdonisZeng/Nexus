"""Agents management commands for /agents (list, create, edit, delete)"""
import json
import re
from pathlib import Path
from typing import AsyncIterator, Optional

from src.commands.base import Command, CommandContext, CommandResult, CommandResultType
from src.cli.rich_ui import console, input_with_prompt, print_error_output
from src.tools.subagent import SubagentConfig, SubagentRegistry
from src.tools import global_registry


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
        from src.utils.frontmatter import serialize_frontmatter
        content = serialize_frontmatter(frontmatter, config.system_prompt.strip())

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
        adapter,
        inherited_tools: Optional[list[str]] = None
    ) -> SubagentConfig:
        """Auto-generate agent configuration from user description via LLM."""
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


# ---------------------------------------------------------------------------
# Create flow
# ---------------------------------------------------------------------------

async def select_tools_multi(available_tools: list[str]) -> list[str]:
    """Show tool list and let user select multiple tools by number"""
    console.print("\n[bold]选择允许的工具（输入编号，空格分隔，如 1 3 5）：[/bold]")
    console.print("[dim]输入 0 确认选择[/dim]\n")

    for i, tool in enumerate(available_tools, 1):
        console.print(f"  [{i}] {tool}")

    selected = []
    while True:
        choice = input_with_prompt("> ").strip()
        if not choice:
            continue

        if choice == "0":
            break

        try:
            nums = [int(x) for x in choice.split()]
            for num in nums:
                if 1 <= num <= len(available_tools):
                    tool = available_tools[num - 1]
                    if tool not in selected:
                        selected.append(tool)
        except ValueError:
            print_error_output("请输入有效的编号")

        console.print(f"\n已选择: {selected if selected else '无'}")

    return selected


async def create_agent_auto(context: CommandContext) -> AsyncIterator[CommandResult]:
    """Auto-create agent flow"""
    yield CommandResult(type=CommandResultType.OUTPUT, content="[bold]自动创建子代理[/bold]\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content="请描述你想要创建的子代理的用途...\n")

    raw_description = input_with_prompt("描述: ").strip()
    if not raw_description:
        yield CommandResult(type=CommandResultType.ERROR, content="描述不能为空")
        return

    yield CommandResult(type=CommandResultType.OUTPUT, content="\n正在调用 AI 生成配置...")

    try:
        # Ask about inheritance
        console.print("\n[bold]选择工具继承方式：[/bold]")
        console.print("[1] 完全继承主 Agent 的工具")
        console.print("[2] 手动选择要继承的工具")

        inherit_choice = input_with_prompt("> ").strip()

        inherited_tools = []
        if inherit_choice == "1":
            inherited_tools = AgentConfigEditor.get_main_agent_tools()
        elif inherit_choice == "2":
            available = AgentConfigEditor.get_available_tools()
            inherited_tools = await select_tools_multi(available)
        else:
            yield CommandResult(type=CommandResultType.ERROR, content="无效选择")
            return

        # Generate agent using LLM
        adapter = context.cli.model_adapter if context.cli else None
        config = await AgentConfigEditor.auto_generate_agent(raw_description, adapter, inherited_tools)

        # Show generated config
        console.print("\n[bold green]生成的配置：[/bold green]")
        console.print(f"  Name: {config.name}")
        console.print(f"  Description: {config.description}")
        console.print(f"  Allowed Tools: {config.allowed_tools}")
        console.print(f"\n[bold]System Prompt:[/bold]")
        console.print(f"  {config.system_prompt[:200]}...")

        # Confirm save
        console.print("\n[bold]确认保存？[/bold] (y/n)")
        confirm = input_with_prompt("> ").strip().lower()

        if confirm == "y":
            AgentConfigEditor.save_agent(config)
            yield CommandResult(type=CommandResultType.SUCCESS, content=f"子代理 '{config.name}' 创建成功！")
        else:
            yield CommandResult(type=CommandResultType.OUTPUT, content="已取消")

    except Exception as e:
        yield CommandResult(type=CommandResultType.ERROR, content=f"创建失败: {str(e)}")


async def create_agent_manual(context: CommandContext) -> AsyncIterator[CommandResult]:
    """Manual create agent flow"""
    yield CommandResult(type=CommandResultType.OUTPUT, content="[bold]手动创建子代理[/bold]\n")

    name = input_with_prompt("Name: ").strip()
    if not name:
        yield CommandResult(type=CommandResultType.ERROR, content="Name 不能为空")
        return

    existing = AgentConfigEditor.load_all_agents()
    if any(a.name == name for a in existing):
        yield CommandResult(type=CommandResultType.ERROR, content=f"Agent '{name}' 已存在")
        return

    description = input_with_prompt("Description: ").strip()
    if not description:
        yield CommandResult(type=CommandResultType.ERROR, content="Description 不能为空")
        return

    # Get system prompt (multi-line)
    console.print("\nSystem Prompt (输入空行 + . 结束)：")
    system_lines = []
    while True:
        line = input_with_prompt("| ").strip()
        if line == ".":
            break
        system_lines.append(line)
    system_prompt = "\n".join(system_lines)
    if not system_prompt:
        yield CommandResult(type=CommandResultType.ERROR, content="System Prompt 不能为空")
        return

    available = AgentConfigEditor.get_available_tools()
    allowed_tools = await select_tools_multi(available)

    # Optional fields
    console.print("\n[dim]以下为可选字段，直接回车使用默认值[/dim]")

    model = input_with_prompt("Model (可选): ").strip() or None

    max_iterations_str = input_with_prompt("Max Iterations (默认10): ").strip()
    max_iterations = int(max_iterations_str) if max_iterations_str else 10

    timeout_str = input_with_prompt("Timeout Seconds (默认300): ").strip()
    timeout_seconds = float(timeout_str) if timeout_str else 300.0

    config = AgentConfigEditor.build_agent_from_input(
        name=name,
        description=description,
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        model=model,
        max_iterations=max_iterations,
        timeout_seconds=timeout_seconds,
    )

    try:
        AgentConfigEditor.save_agent(config)
        yield CommandResult(type=CommandResultType.SUCCESS, content=f"子代理 '{name}' 创建成功！")
    except Exception as e:
        yield CommandResult(type=CommandResultType.ERROR, content=f"保存失败: {str(e)}")


async def run_create_flow(context: CommandContext) -> AsyncIterator[CommandResult]:
    """Run the create agent flow"""
    console.print("\n[bold]创建方式：[/bold]")
    console.print("[1] 自动创建 (推荐)")
    console.print("[2] 手动创建")

    choice = input_with_prompt("> ").strip()

    if choice == "1":
        async for result in create_agent_auto(context):
            yield result
    elif choice == "2":
        async for result in create_agent_manual(context):
            yield result
    else:
        yield CommandResult(type=CommandResultType.ERROR, content="无效选择")


# ---------------------------------------------------------------------------
# Edit flow
# ---------------------------------------------------------------------------

async def view_agent(config) -> AsyncIterator[CommandResult]:
    """View agent details"""
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"[bold]{config.name} 配置详情[/bold]\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"  Description: {config.description}\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"  Allowed Tools: {config.allowed_tools or '全部'}\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"  Denied Tools: {config.denied_tools or '无'}\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"  Model: {config.model or '默认'}\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"  Max Iterations: {config.max_iterations}\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"  Timeout: {config.timeout_seconds}s\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"\n[bold]System Prompt:[/bold]\n{config.system_prompt}")


async def edit_agent(config) -> AsyncIterator[CommandResult]:
    """Edit agent configuration"""
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"[bold]编辑 {config.name}[/bold]\n")
    yield CommandResult(type=CommandResultType.OUTPUT, content="[dim]直接回车保持不变，输入新值覆盖[/dim]\n")

    # Description
    new_desc = input_with_prompt(f"Description [{config.description[:30]}...]: ").strip()
    if new_desc:
        config.description = new_desc

    # System prompt
    console.print(f"\n当前 System Prompt ({len(config.system_prompt)} 字符)")
    console.print("[dim]输入 . 保持不变，输入新内容替换[/dim]")
    for i, line in enumerate(config.system_prompt.split("\n")[:3]):
        console.print(f"  {line}")
    if len(config.system_prompt.split("\n")) > 3:
        console.print("  ...")

    prompt_input = []
    console.print("\n输入新 System Prompt (输入空行 + . 结束):")
    while True:
        line = input_with_prompt("| ").strip()
        if line == ".":
            break
        prompt_input.append(line)

    if prompt_input:
        config.system_prompt = "\n".join(prompt_input)

    # Tools
    available = AgentConfigEditor.get_available_tools()
    console.print(f"\n当前 Allowed Tools: {config.allowed_tools or '全部'}")
    console.print("输入 0 保持不变，否则重新选择")
    new_tools = await select_tools_multi(available)
    if new_tools:
        config.allowed_tools = new_tools

    # Model
    new_model = input_with_prompt(f"Model [{config.model or '默认'}]: ").strip()
    if new_model:
        config.model = new_model or None

    # Save
    try:
        AgentConfigEditor.save_agent(config)
        yield CommandResult(type=CommandResultType.SUCCESS, content=f"Agent '{config.name}' 已更新")
    except Exception as e:
        yield CommandResult(type=CommandResultType.ERROR, content=f"保存失败: {str(e)}")


async def adjust_tools(config) -> AsyncIterator[CommandResult]:
    """Adjust agent tools - add denied tools"""
    yield CommandResult(type=CommandResultType.OUTPUT, content=f"[bold]调整 {config.name} 工具权限[/bold]\n")

    console.print(f"当前 Allowed Tools: {config.allowed_tools or '全部工具'}\n")

    available = [t for t in AgentConfigEditor.get_available_tools()
                 if t not in (config.allowed_tools or [])]

    if not available:
        yield CommandResult(type=CommandResultType.OUTPUT, content="没有可拒绝的工具")
        return

    console.print("选择要拒绝的工具（输入编号，空格分隔）：")
    console.print("[dim]输入 0 确认选择[/dim]\n")

    for i, tool in enumerate(available, 1):
        console.print(f"  [{i}] {tool}")

    denied = []
    while True:
        choice = input_with_prompt("> ").strip()
        if not choice:
            continue

        if choice == "0":
            break

        try:
            nums = [int(x) for x in choice.split()]
            for num in nums:
                if 1 <= num <= len(available):
                    tool = available[num - 1]
                    if tool not in denied:
                        denied.append(tool)
        except ValueError:
            pass

        console.print(f"\n已选择拒绝: {denied if denied else '无'}")

    if denied:
        config.denied_tools = denied
        try:
            AgentConfigEditor.save_agent(config)
            yield CommandResult(type=CommandResultType.SUCCESS, content=f"已更新工具权限")
        except Exception as e:
            yield CommandResult(type=CommandResultType.ERROR, content=f"保存失败: {str(e)}")
    else:
        yield CommandResult(type=CommandResultType.OUTPUT, content="未做任何更改")


async def delete_agent(config) -> AsyncIterator[CommandResult]:
    """Delete an agent"""
    console.print(f"\n[bold red]确认删除 Agent '{config.name}'？[/bold red]")
    console.print("此操作不可恢复！")

    confirm = input_with_prompt("输入 'yes' 确认: ").strip()
    if confirm.lower() == "yes":
        if AgentConfigEditor.delete_agent(config.name):
            yield CommandResult(type=CommandResultType.SUCCESS, content=f"Agent '{config.name}' 已删除")
        else:
            yield CommandResult(type=CommandResultType.ERROR, content="删除失败")
    else:
        yield CommandResult(type=CommandResultType.OUTPUT, content="已取消")


async def run_edit_flow(config) -> AsyncIterator[CommandResult]:
    """Run the edit flow for a specific agent"""
    while True:
        console.print(f"\n[bold]Agent: {config.name}[/bold]")
        console.print("[1] View")
        console.print("[2] Edit")
        console.print("[3] Adjust Tools")
        console.print("[4] Delete")
        console.print("[0] 返回")

        choice = input_with_prompt("> ").strip()

        if choice == "0":
            break
        elif choice == "1":
            async for result in view_agent(config):
                yield result
        elif choice == "2":
            async for result in edit_agent(config):
                yield result
        elif choice == "3":
            async for result in adjust_tools(config):
                yield result
        elif choice == "4":
            async for result in delete_agent(config):
                yield result
            break  # Exit after delete
        else:
            yield CommandResult(type=CommandResultType.ERROR, content="无效选择")


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

class AgentsCommand(Command):
    """Manage subagents"""

    name = "agents"
    description = "管理子代理 (list, create, edit, delete)"
    aliases = ["agent"]

    async def execute(self, context: CommandContext) -> AsyncIterator[CommandResult]:
        """Execute /agents command"""
        agents = AgentConfigEditor.load_all_agents()

        # Main menu
        console.print("\n[bold]SubAgent 管理[/bold]\n")
        console.print("[1] Create SubAgent")
        console.print(f"[2+] 已有 Agent ({len(agents)}个)")

        for i, agent in enumerate(agents, 3):
            desc_preview = agent.description[:40] if len(agent.description) > 40 else agent.description
            console.print(f"[{i}] {agent.name} - {desc_preview}")

        console.print("[0] 退出")

        choice = input_with_prompt("> ").strip()

        if choice == "0":
            yield CommandResult(type=CommandResultType.OUTPUT, content="已退出")
            return

        if choice == "1":
            async for result in run_create_flow(context):
                yield result
            return

        # Select existing agent
        try:
            idx = int(choice) - 3
            if 0 <= idx < len(agents):
                config = agents[idx]
                async for result in run_edit_flow(config):
                    yield result
            else:
                yield CommandResult(type=CommandResultType.ERROR, content="无效选择")
        except ValueError:
            yield CommandResult(type=CommandResultType.ERROR, content="请输入有效编号")


# Singleton instance
agents_command = AgentsCommand()


__all__ = ["agents_command", "AgentConfigEditor", "AGENTS_DIR"]
