"""Bottom input bar with agent routing."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Input, Label
from textual.message import Message


class ComposeBar(Widget):
    DEFAULT_CSS = """
    ComposeBar {
        height: 3;
        border-top: solid $panel;
        layout: horizontal;
        align: left middle;
    }
    #prompt-label {
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #message-input {
        width: 1fr;
        border: none;
        background: transparent;
    }
    """

    class Submitted(Message):
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        yield Label("> ", id="prompt-label")
        yield Input(placeholder="Type a message…", id="message-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.post_message(self.Submitted(text))
            event.input.clear()

    def focus_input(self) -> None:
        self.query_one(Input).focus()
