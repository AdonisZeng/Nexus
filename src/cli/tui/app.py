"""Textual TUI application for Nexus — a Claude-Code-style full-screen UI.

Layout (top to bottom):
    NexusHeader   — title · model · mode · live status
    #chat         — scrolling message stream (user/assistant/tool cards)
    #status-line  — single-line status text
    #input        — bottom input box with /command suggestions
    Footer        — key binding hints
"""
import asyncio
import logging
import uuid
from collections import deque
from typing import Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Input, Markdown, Static

from src.agent import AgentEvent, EventType
from src.cli.tui.sink import TextualOutputSink, StreamEnded, StreamUpdated
from src.cli.tui.widgets import CommandSuggester, NexusHeader, ToolCallCard

logger = logging.getLogger("Nexus")

# Commands that need an interactive terminal; not supported inside the TUI.
_INTERACTIVE_COMMANDS = {
    "settings", "mcpstatus", "plan", "tasks", "teams", "agents",
    "agents_create", "agents_edit", "prompt", "search", "docs", "test",
}


class NexusApp(App):
    """A Claude-Code-style full-screen TUI for Nexus."""

    TITLE = "Nexus"

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "中断 / 退出", priority=True),
        Binding("ctrl+l", "clear_chat", "清空对话"),
        Binding("ctrl+r", "focus_input", "聚焦输入框"),
    ]

    CSS = """
    NexusApp { background: $background; }

    NexusHeader {
        height: 1;
        background: $panel;
        color: $text;
    }
    NexusHeader .header-item { padding: 0 1; }
    #header-title { color: $accent; text-style: bold; }
    #header-model { color: $text-muted; }
    #header-mode { color: $secondary; }
    #header-status { color: $success; }

    #chat {
        height: 1fr;
        padding: 0 2 1 2;
    }

    #status-line {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    #input {
        height: 3;
        margin: 0 2 1 2;
        border: tall $primary;
        background: $surface;
    }

    Footer { height: 1; }

    .user-message {
        margin: 1 0 0 0;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    .assistant-message { margin: 1 0; }
    .error-message { color: $error; margin: 1 0; }
    .warning-message { color: $warning; margin: 1 0; }
    .system-message { color: $text-muted; margin: 1 0; }

    ToolCallCard {
        margin: 1 0;
        border: round $primary;
    }
    ToolCallCard .tool-result { padding: 0 2; }
    """

    def __init__(self, cli, config_path: str = "config.yaml") -> None:
        super().__init__()
        self.cli = cli
        self.config_path = config_path
        self._sink: Optional[TextualOutputSink] = None
        self._busy = False
        self._worker = None
        self._stream_md: Optional[Markdown] = None
        self._stream_flush_timer = None
        self._pending_cards: deque[ToolCallCard] = deque()
        self._seq = 0
        self._commands = self._load_commands()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def _load_commands(self) -> list[str]:
        try:
            from src.commands import get_command_registry
            return ["/" + c.name for c in get_command_registry().get_all()]
        except Exception:
            return ["/help", "/exit", "/clear", "/models", "/sessions", "/restore", "/reload"]

    def compose(self) -> ComposeResult:
        yield NexusHeader()
        with VerticalScroll(id="chat"):
            pass
        yield Static("", id="status-line")
        yield Input(
            placeholder="输入消息，/ 查看命令，Ctrl+C 中断或退出",
            id="input",
            suggester=CommandSuggester(self._commands),
        )
        yield Footer()

    def on_mount(self) -> None:
        from src.utils.output import set_output_sink

        self._sink = TextualOutputSink(self)
        set_output_sink(self._sink)

        self._chat = self.query_one("#chat", VerticalScroll)
        self._input = self.query_one("#input", Input)
        self._header = self.query_one(NexusHeader)

        self._header.set_model(
            self.cli.model_adapter.get_name() if self.cli.model_adapter else "?"
        )
        self._header.set_mode("normal")
        self._header.set_status("● 就绪")
        self._input.focus()
        self._add_system("欢迎使用 Nexus · 输入 /help 查看命令")

    def on_unmount(self) -> None:
        from src.utils.output import SilentOutputSink, set_output_sink

        set_output_sink(SilentOutputSink())

    # ── input handling ─────────────────────────────────────────────────────

    @on(Input.Submitted)
    def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        if self._busy:
            self.notify("任务执行中，请稍候…", severity="warning")
            return
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        lower = text.lower()
        if lower in ("/exit", "/quit", "exit", "/bye"):
            self._do_shutdown()
            return
        if lower in ("/clear", "/cls"):
            self._clear_chat()
            return
        if text.startswith("/"):
            self._run_command(text)
            return
        self._add_user_message(text)
        self._run_task(text)

    def action_interrupt(self) -> None:
        if self._busy and self._worker:
            self._header.set_status("● 中断中…")
            self._worker.cancel()
        else:
            self._do_shutdown()

    def action_clear_chat(self) -> None:
        self._clear_chat()

    def action_focus_input(self) -> None:
        self._input.focus()

    # ── task execution ─────────────────────────────────────────────────────

    def _run_task(self, text: str) -> None:
        self._set_busy(True)
        self._worker = self.run_worker(
            self._execute_task(text), exclusive=True, group="task", exit_on_error=False
        )

    async def _execute_task(self, text: str) -> None:
        try:
            async for event in self.cli.execute_task(text):
                await self._handle_event(event)
        except asyncio.CancelledError:
            self._add_system("任务已中断")
        except Exception as e:
            logger.exception("task failed")
            self._add_error(f"执行出错: {e}")
        finally:
            self._finish_stream()
            self._set_busy(False)

    async def _handle_event(self, event: AgentEvent) -> None:
        if event.type == EventType.THINKING:
            self._header.set_status(f"🤔 {event.content}")
        elif event.type == EventType.TOOL_CALL:
            self._add_tool_card(event)
        elif event.type == EventType.TOOL_RESULT:
            self._finish_tool_card(event)
        elif event.type == EventType.OUTPUT:
            self._add_assistant_text(event.content)
        elif event.type == EventType.ERROR:
            self._add_error(event.content)
        elif event.type == EventType.WARNING:
            self._add_warning(event.content)
        elif event.type == EventType.DONE:
            self._header.set_status("● 就绪")

    # ── chat rendering helpers ─────────────────────────────────────────────

    def _add_user_message(self, text: str) -> None:
        self._chat.mount(Static(f"› {text}", classes="user-message", markup=False))
        self._scroll_end()

    def _add_assistant_text(self, text: str) -> None:
        if not text:
            return
        md = Markdown(text, classes="assistant-message")
        self._chat.mount(md)
        self._scroll_end()

    def _add_error(self, text: str) -> None:
        self._chat.mount(Static(f"✗ {text}", classes="error-message", markup=False))
        self._scroll_end()

    def _add_warning(self, text: str) -> None:
        self._chat.mount(Static(f"⚠ {text}", classes="warning-message", markup=False))
        self._scroll_end()

    def _add_system(self, text: str) -> None:
        self._chat.mount(Static(text, classes="system-message", markup=False))
        self._scroll_end()

    def _scroll_end(self) -> None:
        try:
            self._chat.scroll_end(animate=False)
        except Exception:
            pass

    def _add_tool_card(self, event: AgentEvent) -> None:
        tool_name = event.metadata.get("tool_name", event.content)
        args = event.metadata.get("args") or {}
        card = ToolCallCard(tool_name, args, self._seq)
        self._seq += 1
        self._pending_cards.append(card)
        self._chat.mount(card)
        self._scroll_end()

    def _finish_tool_card(self, event: AgentEvent) -> None:
        if not self._pending_cards:
            return
        card = self._pending_cards.popleft()
        content = event.content or ""
        card.set_result(content, error=content.startswith("Error"))
        self._scroll_end()

    # ── streaming (fed by TextualOutputSink) ───────────────────────────────

    @on(StreamUpdated)
    def _on_stream_updated(self, _msg: StreamUpdated) -> None:
        self._schedule_stream_flush()

    @on(StreamEnded)
    def _on_stream_ended(self, _msg: StreamEnded) -> None:
        self._flush_stream()
        self._end_stream_block()

    def _schedule_stream_flush(self) -> None:
        if self._stream_flush_timer is None:
            self._stream_flush_timer = self.set_timer(0.04, self._flush_stream)

    def _flush_stream(self) -> None:
        self._stream_flush_timer = None
        if not self._sink:
            return
        text = self._sink.get_buffer()
        if not text:
            return
        if self._stream_md is None:
            self._stream_md = Markdown("", classes="assistant-message")
            self._chat.mount(self._stream_md)
        self._stream_md.update(text)
        self._scroll_end()

    def _end_stream_block(self) -> None:
        self._stream_md = None

    def _finish_stream(self) -> None:
        if self._stream_md is not None:
            self._flush_stream()
            self._end_stream_block()

    # ── commands ───────────────────────────────────────────────────────────

    def _run_command(self, text: str) -> None:
        self._set_busy(True)
        self._worker = self.run_worker(
            self._execute_command(text), exclusive=True, group="command", exit_on_error=False
        )

    async def _execute_command(self, text: str) -> None:
        from src.commands import CommandContext, get_command_registry

        registry = get_command_registry()
        cmd_name, cmd, args = registry.parse_input(text)
        if not cmd:
            self._add_error(f"未知命令: /{cmd_name}")
            return
        if cmd_name in _INTERACTIVE_COMMANDS:
            self._add_warning(
                f"/{cmd_name} 依赖交互式终端，在 TUI 中不可用"
            )
            return

        try:
            context = CommandContext(
                args=args,
                cli=self.cli,
                session_id=self.cli.session_id,
                session={"messages": self.cli.messages},
            )
            async for result in cmd.execute(context):
                self._render_command_result(result)
        except Exception as e:
            logger.exception("command failed")
            self._add_error(f"命令执行失败: {e}")
        finally:
            self._set_busy(False)

        if cmd_name == "restore":
            self._render_messages()

    def _render_command_result(self, result) -> None:
        t = result.type.value
        if t == "error":
            self._add_error(result.content)
        elif t == "warning":
            self._add_warning(result.content)
        elif t in ("output", "success"):
            self._add_assistant_text(result.content)
        elif t == "thinking":
            self._header.set_status(result.content)

    def _render_messages(self) -> None:
        self._chat.remove_children()
        self._stream_md = None
        for msg in self.cli.messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user" and content:
                self._add_user_message(content)
            elif role == "assistant" and content:
                self._add_assistant_text(content)

    # ── session / app control ──────────────────────────────────────────────

    def _clear_chat(self) -> None:
        self.cli.messages = []
        self.cli.session_id = str(uuid.uuid4())
        self.cli.current_title = "新对话"
        self._chat.remove_children()
        self._stream_md = None
        self._pending_cards.clear()
        self._add_system("已开启新对话")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._input.disabled = busy
        if busy:
            self._header.set_status("● 运行中")
        else:
            self._header.set_status("● 就绪")

    def _save_session(self) -> None:
        try:
            if self.cli.messages:
                self.cli.memory_manager.save_session(
                    self.cli.session_id, self.cli.messages, self.cli.current_title
                )
        except Exception as e:
            logger.warning(f"保存会话失败: {e}")

    def _do_shutdown(self) -> None:
        self._save_session()
        self.exit()


__all__ = ["NexusApp"]
