"""Edit agent flow for /agents command"""
from typing import AsyncIterator

from src.commands.base import CommandContext, CommandResult, CommandResultType
from src.cli.rich_ui import console, input_with_prompt
from .agents_utils import AgentConfigEditor
from .agents_create import select_tools_multi


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
    new_prompt_lines = []
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

    # Show current allowed tools
    console.print(f"当前 Allowed Tools: {config.allowed_tools or '全部工具'}\n")

    # Get available tools to deny
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


__all__ = ["run_edit_flow"]
