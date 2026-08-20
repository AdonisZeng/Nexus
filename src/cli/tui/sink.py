"""TextualOutputSink — route agent streaming output into the Textual app.

The agent engine (``AgentSession._execute_task_streaming``) writes streaming
text through the global ``OutputSink``. In TUI mode we install this sink so
the text is buffered here and forwarded to the app via lightweight messages;
the app throttles actual widget updates to keep streaming smooth.
"""
import threading

from textual.message import Message

from src.utils.output import OutputSink


class StreamUpdated(Message):
    """Posted when the sink has buffered new streaming text."""


class StreamEnded(Message):
    """Posted when the current streaming block is complete."""


class TextualOutputSink(OutputSink):
    """OutputSink implementation that forwards output to a Textual app.

    The engine calls these methods from inside a Textual worker, which runs
    on the same event loop as the UI. We only buffer text and post lightweight
    messages — never touch widgets directly — so the app can throttle
    rendering on its own schedule.
    """

    def __init__(self, app) -> None:
        self._app = app
        self._buffer = ""
        self._lock = threading.Lock()

    # -- streaming interface used by AgentSession._execute_task_streaming --

    def start_streaming(self) -> None:
        with self._lock:
            self._buffer = ""
        self._app.post_message(StreamUpdated())

    def print_streaming_text(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._buffer += text
        self._app.post_message(StreamUpdated())

    def print_streaming_line(self) -> None:
        self._app.post_message(StreamEnded())

    def clear_streaming_buffer(self) -> None:
        with self._lock:
            self._buffer = ""

    # -- generic OutputSink interface --

    def print(self, message: str, **kwargs) -> None:
        with self._lock:
            self._buffer += str(message)
        self._app.post_message(StreamUpdated())

    def input(self, prompt: str = "") -> str:
        return ""

    def get_console(self):
        return None

    def get_buffer(self) -> str:
        with self._lock:
            return self._buffer


__all__ = ["TextualOutputSink", "StreamUpdated", "StreamEnded"]
