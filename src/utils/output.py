"""Output abstraction: decouple non-CLI modules from Rich console."""
from typing import Protocol, runtime_checkable


@runtime_checkable
class OutputSink(Protocol):
    def print(self, message: str, **kwargs) -> None: ...
    def input(self, prompt: str = "") -> str: ...
    def start_streaming(self) -> None: ...
    def print_streaming_text(self, text: str) -> None: ...
    def print_streaming_line(self) -> None: ...
    def clear_streaming_buffer(self) -> None: ...
    def get_console(self): ...


class SilentOutputSink:
    def print(self, message: str, **kwargs) -> None:
        pass

    def input(self, prompt: str = "") -> str:
        return ""

    def start_streaming(self) -> None:
        pass

    def print_streaming_text(self, text: str) -> None:
        pass

    def print_streaming_line(self) -> None:
        pass

    def clear_streaming_buffer(self) -> None:
        pass

    def get_console(self):
        return None


class RichOutputSink:
    def __init__(self, console=None):
        from src.cli.rich_ui import console as _c, input_with_prompt as _ip, \
            start_streaming as _ss, print_streaming_text as _pst, \
            print_streaming_line as _psl, clear_streaming_buffer as _csb
        self._console = console if console is not None else _c
        self._input_with_prompt = _ip
        self._start_streaming = _ss
        self._print_streaming_text = _pst
        self._print_streaming_line = _psl
        self._clear_streaming_buffer = _csb

    def print(self, message: str, **kwargs) -> None:
        self._console.print(message, **kwargs)

    def input(self, prompt: str = "") -> str:
        return self._input_with_prompt(prompt)

    def start_streaming(self) -> None:
        self._start_streaming()

    def print_streaming_text(self, text: str) -> None:
        self._print_streaming_text(text)

    def print_streaming_line(self) -> None:
        self._print_streaming_line()

    def clear_streaming_buffer(self) -> None:
        self._clear_streaming_buffer()

    def get_console(self):
        return self._console


_default_sink: OutputSink = SilentOutputSink()


def get_output_sink() -> OutputSink:
    return _default_sink


def set_output_sink(sink: OutputSink) -> None:
    global _default_sink
    _default_sink = sink


__all__ = ["OutputSink", "SilentOutputSink", "RichOutputSink", "get_output_sink", "set_output_sink"]
