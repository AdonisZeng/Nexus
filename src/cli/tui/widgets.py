"""Custom widgets for the Nexus Textual TUI."""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.suggester import Suggester
from textual.widgets import Collapsible, Static


class CommandSuggester(Suggester):
    """Suggests `/command` completions while the user is typing."""

    def __init__(self, commands: list[str]) -> None:
        super().__init__(case_sensitive=False)
        self._commands = sorted(set(commands), key=len)

    async def get_suggestion(self, value: str) -> Optional[str]:
        if not value.startswith("/"):
            return None
        for command in self._commands:
            if command.lower().startswith(value.lower()):
                return command
        return None


class NexusHeader(Horizontal):
    """Top status bar: app title · model · mode · live status."""

    def compose(self) -> ComposeResult:
        yield Static("Nexus", id="header-title", classes="header-item")
        yield Static("", id="header-model", classes="header-item")
        yield Static("", id="header-mode", classes="header-item")
        yield Static("● idle", id="header-status", classes="header-item")

    def set_model(self, name: str) -> None:
        self.query_one("#header-model", Static).update(name)

    def set_mode(self, name: str) -> None:
        self.query_one("#header-mode", Static).update(name)

    def set_status(self, text: str) -> None:
        self.query_one("#header-status", Static).update(text)


class ToolCallCard(Collapsible):
    """A collapsible card rendering a single tool call + result."""

    def __init__(self, tool_name: str, args: Optional[dict], seq: int) -> None:
        self.tool_name = tool_name
        self.args = args or {}
        self.seq = seq
        self.result_static = Static("⏳ 执行中...", classes="tool-result")
        summary = self._args_summary()
        title = f"[{tool_name}]" if not summary else f"[{tool_name}] {summary}"
        super().__init__(
            self.result_static,
            title=title,
            collapsed=False,
            classes="tool-card",
        )

    def _args_summary(self) -> str:
        parts = []
        for k, v in list(self.args.items())[:4]:
            parts.append(f"{k}={str(v)[:40]}")
        text = ", ".join(parts)
        if len(text) > 60:
            text = text[:60] + "…"
        return text

    def set_running(self) -> None:
        self.result_static.update("⏳ 执行中...")
        self.collapsed = False

    def set_result(self, result: str, error: bool = False) -> None:
        result = result or ""
        lines = len(result.splitlines())
        chars = len(result)
        preview = result if len(result) <= 400 else result[:400] + "\n…"
        if error:
            self.result_static.update(f"✗ 失败 · {lines} 行\n\n{preview}")
        else:
            self.result_static.update(f"✓ 完成 · {lines} 行 {chars} 字符\n\n{preview}")
        self.collapsed = True


__all__ = ["CommandSuggester", "NexusHeader", "ToolCallCard"]
