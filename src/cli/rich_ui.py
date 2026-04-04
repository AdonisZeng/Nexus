"""Rich UI utilities for Nexus CLI"""
import threading
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.text import Text
from rich.box import ROUNDED, SIMPLE
from typing import Optional, List, Any
from datetime import datetime

# Global console instance
console = Console()

# Lock for thread-safe streaming buffer access
_streaming_lock = threading.Lock()


def print_success(message: str) -> None:
    """Print success message in green"""
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print error message in red"""
    console.print(f"[red]✗[/red] {message}")


def print_warning(message: str) -> None:
    """Print warning message in yellow"""
    console.print(f"[yellow]⚠[/yellow] {message}")


def print_info(message: str) -> None:
    """Print info message in blue"""
    console.print(f"[blue]ℹ[/blue] {message}")


def print_header(message: str, style: str = "blue") -> None:
    """Print a header message"""
    console.print(f"[{style} bold]{message}[/{style}]")


def print_panel(content: str, title: str = "", style: str = "blue") -> None:
    """Print content in a panel"""
    if title:
        console.print(Panel(content, title=title, border_style=style, box=ROUNDED))
    else:
        console.print(Panel(content, border_style=style, box=ROUNDED))


def print_init_info(provider: str, model: str, memory_dir: str, cwd: str = None) -> None:
    """
    @brief 在面板中打印初始化信息
    @param provider 供应商名称
    @param model 模型名称
    @param memory_dir 记忆目录路径
    @param cwd 工作目录
    """
    from src import __version__

    table = Table(show_header=False, box=SIMPLE, padding=(0, 2))
    table.add_column("key", style="cyan", width=15)
    table.add_column("value", style="white")

    table.add_row("Provider", f"[green]{provider}[/green]")
    table.add_row("Model", f"[green]{model}[/green]")
    if cwd:
        table.add_row("CWD", str(cwd))
    table.add_row("Memory", str(memory_dir))
    table.add_row("Version", f"[dim]{__version__}[/dim]")

    console.print(Panel(
        table,
        title="[bold cyan]Nexus - Personal AI Agent[/bold cyan]",
        border_style="cyan",
        box=ROUNDED
    ))


def print_welcome() -> None:
    """Print welcome message"""
    pass


def print_sessions_table(sessions: List, show_id: bool = True) -> None:
    """Print sessions in a table"""
    if not sessions:
        print_info("暂无保存的会话")
        return

    table = Table(title="[cyan]已保存的会话[/cyan]", box=ROUNDED)
    table.add_column("#", style="dim", width=4)
    table.add_column("标题", style="white")
    table.add_column("时间", style="cyan")
    table.add_column("消息数", style="green", justify="right")

    if show_id:
        table.add_column("ID", style="dim")

    for i, session in enumerate(sessions, 1):
        time_str = datetime.fromtimestamp(session.created_at).strftime('%Y-%m-%d %H:%M')
        row = [
            str(i),
            session.title,
            time_str,
            str(session.message_count)
        ]
        if show_id:
            row.append(f"{session.session_id[:8]}...")
        table.add_row(*row)

    console.print(table)


def print_help(skills: List[str]) -> None:
    """Print help with available commands"""
    table = Table(title="[cyan]可用命令[/cyan]", box=ROUNDED)
    table.add_column("命令", style="yellow", width=15)
    table.add_column("描述", style="white")

    # Built-in commands
    builtins = [
        ("/help", "显示帮助信息"),
        ("/exit", "退出程序"),
        ("/quit", "退出程序"),
        ("/clear", "开始新对话"),
        ("/sessions", "列出保存的会话"),
        ("/restore", "恢复会话"),
        ("/models", "显示当前模型"),
        ("/settings", "配置供应商和模型"),
        ("/mcpstatus", "显示MCP服务器连接状态"),
    ]

    for cmd, desc in builtins:
        table.add_row(cmd, desc)

    # Add skills
    for skill in skills:
        table.add_row(f"/{skill}", "[dim]自定义命令[/dim]")

    console.print(table)


def print_thinking(content: str) -> None:
    """Print thinking status"""
    console.print(f"[blue][thinking][/blue] {content}")


def print_tool_call(tool_name: str, args: dict = None) -> None:
    """
    @brief 打印工具调用信息
    @param tool_name 工具名称
    @param args 工具参数
    """
    if args:
        args_str = ", ".join(f"{k}={repr(v)[:30]}" for k, v in args.items())
        if len(args_str) > 60:
            args_str = args_str[:60] + "..."
        console.print(f"[yellow][tool][/yellow] {tool_name}([dim]{args_str}[/dim])")
    else:
        console.print(f"[yellow][tool][/yellow] {tool_name}()")


def print_tool_result(tool_name: str, result: str, max_len: int = 300, collapsed: bool = True) -> None:
    """
    @brief 打印工具结果（可折叠样式）
    @param tool_name 工具名称
    @param result 工具结果内容
    @param max_len 最大显示长度
    @param collapsed 是否折叠显示
    """
    result_str = str(result) if result else ""
    lines = result_str.split("\n")
    total_lines = len(lines)
    total_chars = len(result_str)
    
    if collapsed:
        if total_lines <= 3 and total_chars <= max_len:
            console.print(Panel(
                result_str,
                title=f"[dim]{tool_name}[/dim]",
                border_style="dim",
                box=ROUNDED,
                padding=(0, 1)
            ))
        else:
            preview_lines = lines[:3]
            preview = "\n".join(preview_lines)
            if len(preview) > max_len:
                preview = preview[:max_len] + "..."
            
            summary = f"[dim]{total_lines} 行, {total_chars} 字符[/dim]"
            console.print(Panel(
                f"{preview}\n\n[dim]... ({summary}, 已折叠)[/dim]",
                title=f"[dim]{tool_name}[/dim]",
                border_style="dim",
                box=ROUNDED,
                padding=(0, 1)
            ))
    else:
        console.print(Panel(
            result_str,
            title=f"[dim]{tool_name}[/dim]",
            border_style="dim",
            box=ROUNDED,
            padding=(0, 1)
        ))


def print_tool_result_simple(content: str, max_len: int = 200) -> None:
    """Print tool result (truncated) - simple version"""
    truncated = content[:max_len] + "..." if len(content) > max_len else content
    console.print(f"  [dim]->[/dim] {truncated}")


def print_output(content: str) -> None:
    """Print agent output in a panel"""
    console.print()
    console.print(Panel(
        content.strip(),
        title="[green]Output[/green]",
        border_style="green",
        box=ROUNDED
    ))


def print_error_output(content: str) -> None:
    """Print error in a panel"""
    console.print(Panel(
        content.strip(),
        title="[red]Error[/red]",
        border_style="red",
        box=ROUNDED
    ))


def print_done(content: str = "任务完成") -> None:
    """Print done status"""
    console.print(f"[dim][done][/dim] {content}")


def print_saved(session_path: str) -> None:
    """Print session saved message"""
    console.print(f"[dim]会话已保存:[/dim] {session_path}")


def confirm(prompt: str) -> bool:
    """Ask for confirmation"""
    response = console.input(f"{prompt} [y/N]: ")
    return response.lower() in ('y', 'yes')


def input_with_prompt(prompt: str) -> str:
    """Get input with styled prompt"""
    return console.input(f"[cyan]{prompt}[/cyan]")


def print_settings_menu() -> None:
    """
    @brief 显示设置主菜单
    @details 显示两个选项：更新供应商信息和更换默认模型
    """
    table = Table(title="[cyan]设置菜单[/cyan]", box=ROUNDED, show_header=False)
    table.add_column("选项", style="yellow", width=4)
    table.add_column("描述", style="white")

    table.add_row("1", "更新供应商信息")
    table.add_row("2", "更换默认模型")

    console.print(table)


def print_provider_select() -> None:
    """
    @brief 显示供应商选择列表
    @details 显示可选的供应商列表，每个选项带编号
    """
    providers = [
        ("1", "Anthropic"),
        ("2", "OpenAI"),
        ("3", "Ollama"),
        ("4", "LM Studio"),
        ("5", "Custom"),
        ("6", "MiniMax"),
        ("7", "xAI"),
    ]

    table = Table(title="[cyan]选择供应商[/cyan]", box=ROUNDED, show_header=False)
    table.add_column("选项", style="yellow", width=4)
    table.add_column("供应商", style="white")

    for opt, name in providers:
        table.add_row(opt, name)

    console.print(table)


def print_provider_config_form(provider: str) -> None:
    """
    @brief 显示供应商配置表单提示
    @details 根据 provider 类型显示不同的输入提示
    @param provider 供应商名称 (anthropic/openai/ollama/lmstudio/custom)
    """
    provider_lower = provider.lower()

    console.print()
    console.print(Panel(
        f"[bold]配置 {provider}[/bold]",
        border_style="cyan",
        box=ROUNDED
    ))

    if provider_lower == "anthropic":
        console.print("[cyan]请输入 API Key:[/cyan]")
        console.print("[dim]提示: API Key 以 sk-ant- 开头[/dim]")
        console.print()
        console.print("[cyan]请输入 Model:[/cyan]")
        console.print("[dim]提示: 如 claude-3-5-sonnet-20241022[/dim]")

    elif provider_lower == "openai":
        console.print("[cyan]请输入 API Key:[/cyan]")
        console.print("[dim]提示: API Key 以 sk- 开头[/dim]")
        console.print()
        console.print("[cyan]请输入 Model:[/cyan]")
        console.print("[dim]提示: 如 gpt-4o, gpt-4o-mini[/dim]")

    elif provider_lower == "ollama":
        console.print("[cyan]请输入 URL:[/cyan]")
        console.print("[dim]提示: 默认为 http://localhost:11434[/dim]")
        console.print()
        console.print("[cyan]请输入 Model (可选):[/cyan]")
        console.print("[dim]提示: 如 llama3.2, qwen2.5[/dim]")

    elif provider_lower == "lmstudio":
        console.print("[cyan]请输入 URL:[/cyan]")
        console.print("[dim]提示: 默认为 http://localhost:1234/v1[/dim]")
        console.print()
        console.print("[cyan]请输入 Model (可选):[/cyan]")
        console.print("[dim]提示: 可留空，由 LM Studio 自动选择[/dim]")

    elif provider_lower == "custom":
        console.print("[cyan]请选择 API 协议:[/cyan]")
        console.print("[dim]提示: OpenAI 协议 (兼容 OpenAI API) 或 Anthropic 协议 (兼容 Anthropic API)[/dim]")
        console.print()
        console.print("[cyan]请输入 Base URL:[/cyan]")
        console.print("[dim]提示: 如 https://api.example.com/v1[/dim]")
        console.print()
        console.print("[cyan]请输入 API Key:[/cyan]")
        console.print("[dim]提示: 根据服务商要求填写[/dim]")
        console.print()
        console.print("[cyan]请输入 Model:[/cyan]")
        console.print("[dim]提示: 根据服务商支持的模型填写[/dim]")


def print_api_protocol_select() -> None:
    """
    @brief 显示 API 协议选择列表
    @details 显示可选的 API 协议选项，用于 Custom 供应商配置
    """
    protocols = [
        ("1", "OpenAI 协议", "兼容 OpenAI Chat Completions"),
        ("2", "Anthropic 协议", "兼容 Anthropic Messages API"),
    ]

    table = Table(title="[cyan]选择 API 协议[/cyan]", box=ROUNDED, show_header=False)
    table.add_column("选项", style="yellow", width=4)
    table.add_column("协议", style="white")
    table.add_column("描述", style="dim")

    for opt, name, desc in protocols:
        table.add_row(opt, name, desc)

    console.print(table)


def print_default_provider_select(providers: list[str], current: str) -> None:
    """
    @brief 显示默认供应商选择列表
    @details 显示已配置的供应商列表，标记当前默认供应商
    @param providers 已配置的供应商列表
    @param current 当前默认供应商
    """
    if not providers:
        print_warning("暂无已配置的供应商")
        return

    table = Table(title="[cyan]选择默认供应商[/cyan]", box=ROUNDED, show_header=False)
    table.add_column("选项", style="yellow", width=4)
    table.add_column("供应商", style="white")
    table.add_column("状态", style="green")

    for i, provider in enumerate(providers, 1):
        if provider.lower() == current.lower():
            table.add_row(str(i), provider, "[green]* 默认[/green]")
        else:
            table.add_row(str(i), provider, "")

    console.print(table)


def print_plan_header() -> None:
    """Print Plan mode header"""
    console.print()
    console.print(Panel(
        "[bold cyan]📋 执行计划[/bold cyan]",
        border_style="cyan",
        box=ROUNDED
    ))


def print_task_list(tasks: list[str], current_index: int = -1, completed_indices: set[int] = None) -> None:
    """
    @brief 打印任务列表
    @param tasks 任务列表
    @param current_index 当前执行的任务索引 (-1 表示无)
    @param completed_indices 已完成的任务索引集合
    """
    if completed_indices is None:
        completed_indices = set()

    from rich.tree import Tree

    tree = Tree("[cyan]执行计划[/cyan]", guide_style="cyan")

    for i, task in enumerate(tasks):
        if i in completed_indices:
            status = "[green][x][/green]"
        elif i == current_index:
            status = "[yellow][>][/yellow]"
        else:
            status = "[dim][ ][/dim]"

        task_text = f"{status} #{i + 1}: {task}"
        tree.add(task_text)

    console.print(tree)

    done_count = len(completed_indices)
    total_count = len(tasks)
    console.print(f"[dim]({done_count}/{total_count} completed)[/dim]")


def print_plan_status(message: str, status_type: str = "thinking") -> None:
    """
    @brief 打印 Plan 模式状态消息
    @param message 状态消息
    @param status_type 状态类型: thinking, executing, analyzing, completed, error
    """
    status_icons = {
        "thinking": "🤔",
        "analyzing": "🔍",
        "executing": "⚙️",
        "completed": "✅",
        "error": "❌",
        "info": "ℹ️"
    }

    icon = status_icons.get(status_type, "ℹ️")
    style_map = {
        "thinking": "blue",
        "analyzing": "cyan",
        "executing": "yellow",
        "completed": "green",
        "error": "red",
        "info": "dim"
    }

    style = style_map.get(status_type, "dim")
    console.print(f"[{style}]{icon} {message}[/{style}]")


def print_plan_detail(tasks: list[str]) -> None:
    """详细显示计划内容"""
    from rich.table import Table

    table = Table(title="[bold cyan]执行计划[/bold cyan]", box=ROUNDED)
    table.add_column("#", style="cyan", width=4)
    table.add_column("步骤", style="white")

    for i, task in enumerate(tasks, 1):
        table.add_row(str(i), task)

    console.print(table)


def print_plan_confirmation(tasks: list[str]) -> None:
    """显示计划并请求用户确认"""
    console.print()
    console.print("[bold]请确认执行计划:[/bold]")
    console.print()

    print_plan_detail(tasks)

    console.print()
    console.print("[bold]选择操作:[/bold]")
    console.print("  [green]y/yes[/green] - 确认执行")
    console.print("  [red]n/no[/red]   - 取消")
    console.print("  [yellow]e/edit[/yellow] - 重新分析")
    console.print("  [blue]s/show[/blue]  - 重新显示")
    console.print()


def print_plan_mode_indicator() -> None:
    """Print the Plan mode input prompt indicator"""
    console.print("[dim]📋 Plan 模式 - 输入任务描述 (Ctrl+C 退出)[/dim]")


def print_tasks_mode_indicator() -> None:
    """Print the Tasks mode input prompt indicator"""
    console.print("[dim]📊 Tasks 模式 - 输入复杂任务描述 (Ctrl+C 退出)[/dim]")


def print_tasks_status(message: str, status_type: str = "thinking") -> None:
    """Print Tasks mode status message

    @param message: Status message
    @param status_type: Status type: thinking, analyzing, executing, completed, error
    """
    status_icons = {
        "thinking": "🤔",
        "analyzing": "🔍",
        "executing": "⚙️",
        "completed": "✅",
        "error": "❌",
        "info": "ℹ️",
        "warning": "⚠️",
    }
    icon = status_icons.get(status_type, "ℹ️")
    colors = {
        "thinking": "dim",
        "analyzing": "cyan",
        "executing": "yellow",
        "completed": "green",
        "error": "red",
        "info": "blue",
        "warning": "yellow",
    }
    color = colors.get(status_type, "white")
    console.print(f"[{color}]{icon} {message}[/{color}]")


def print_tasks_progress(completed: int, total: int) -> None:
    """Print Tasks progress

    @param completed: Number of completed tasks
    @param total: Total number of tasks
    """
    percentage = (completed / total * 100) if total > 0 else 0
    bar_width = 20
    filled = int(bar_width * completed / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    console.print(f"\n[cyan]进度: [{bar}] {completed}/{total} ({percentage:.0f}%)[/cyan]\n")


def print_task_dependency_graph(tasks: list) -> None:
    """Print task dependency graph

    @param tasks: List of Task objects
    """
    from rich.tree import Tree
    from rich.table import Table

    if not tasks:
        return

    console.print("\n[bold cyan]📊 任务依赖图[/bold cyan]\n")

    table = Table(title="[bold]任务列表[/bold]", box=ROUNDED)
    table.add_column("#", style="cyan", width=4)
    table.add_column("任务", style="white")
    table.add_column("状态", style="yellow", width=10)
    table.add_column("依赖", style="red", width=15)

    for task in sorted(tasks, key=lambda t: t.id):
        status_marker = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
        }.get(task.status, "[?]")

        blocked_by_str = str(task.blocked_by) if task.blocked_by else "-"

        table.add_row(
            str(task.id),
            f"{status_marker} {task.subject}",
            task.status,
            blocked_by_str,
        )

    console.print(table)


def print_tasks_confirmation(tasks: list) -> None:
    """Display tasks and request confirmation

    @param tasks: List of tasks to confirm
    """
    console.print()
    console.print("[bold]请确认任务计划:[/bold]")
    console.print()

    from rich.table import Table

    table = Table(title="[bold cyan]任务计划[/bold cyan]", box=ROUNDED)
    table.add_column("#", style="cyan", width=4)
    table.add_column("任务", style="white")
    table.add_column("依赖", style="red", width=15)

    for task in tasks:
        blocked_by = task.get("blocked_by", [])
        blocked_by_str = str(blocked_by) if blocked_by else "-"
        table.add_row(str(task.get("id", "")), task.get("subject", ""), blocked_by_str)

    console.print(table)


# Streaming output support
_streaming_buffer = ""


def start_streaming() -> None:
    """Start a new streaming output session"""
    global _streaming_buffer
    with _streaming_lock:
        _streaming_buffer = ""


def print_streaming_text(text: str, end: str = "") -> None:
    """Print streaming text without new line, accumulates in buffer"""
    global _streaming_buffer
    with _streaming_lock:
        _streaming_buffer += text
    console.print(text, end=end)


def print_streaming_line(text: str = "") -> None:
    """Print a line during streaming (adds new line)"""
    console.print(text)


def get_streaming_buffer() -> str:
    """Get the current streaming buffer content"""
    global _streaming_buffer
    with _streaming_lock:
        return _streaming_buffer


def clear_streaming_buffer() -> None:
    """Clear the streaming buffer"""
    global _streaming_buffer
    with _streaming_lock:
        _streaming_buffer = ""