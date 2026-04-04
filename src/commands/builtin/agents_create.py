"""Create agent flow for /agents command"""
from typing import AsyncIterator, Optional
from pathlib import Path

from src.commands.base import CommandContext, CommandResult, CommandResultType
from src.cli.rich_ui import console, input_with_prompt, print_error_output
from src.tools.subagent import SubagentConfig
from .agents_utils import AgentConfigEditor


async def select_tools_multi(available_tools: list[str]) -> list[str]:
    """Show tool list and let user select multiple tools by number"""
    console.print("\n[bold]选择允许的工具（输入编号，空格分隔，如 1 3 5）：[/bold]")
    console.print("[dim]输入 0 确认选择[/dim]\n")

    # Show tool list with numbers
    for i, tool in enumerate(available_tools, 1):
        console.print(f"  [{i}] {tool}")

    selected = []
    while True:
        choice = input_with_prompt("> ").strip()
        if not choice:
            continue

        if choice == "0":
            break

        # Parse numbers
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

    # Get description from user
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
        config = await AgentConfigEditor.auto_generate_agent(raw_description, inherited_tools)

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

    # Get name
    name = input_with_prompt("Name: ").strip()
    if not name:
        yield CommandResult(type=CommandResultType.ERROR, content="Name 不能为空")
        return

    # Check if name already exists
    existing = AgentConfigEditor.load_all_agents()
    if any(a.name == name for a in existing):
        yield CommandResult(type=CommandResultType.ERROR, content=f"Agent '{name}' 已存在")
        return

    # Get description
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

    # Get allowed tools
    available = AgentConfigEditor.get_available_tools()
    allowed_tools = await select_tools_multi(available)

    # Optional fields
    console.print("\n[dim]以下为可选字段，直接回车使用默认值[/dim]")

    model = input_with_prompt("Model (可选): ").strip() or None

    max_iterations_str = input_with_prompt("Max Iterations (默认10): ").strip()
    max_iterations = int(max_iterations_str) if max_iterations_str else 10

    timeout_str = input_with_prompt("Timeout Seconds (默认300): ").strip()
    timeout_seconds = float(timeout_str) if timeout_str else 300.0

    # Build and save
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


__all__ = ["run_create_flow"]
