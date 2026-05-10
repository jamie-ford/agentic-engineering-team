"""DM (direct message) screen — private conversation with one agent."""

from __future__ import annotations

import asyncio
from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Button

from .. import logger
from ..agent import Agent
from ..names import colour_for
from .bubble import MessageBubble, SystemMessage


class DmScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=True),
        Binding("ctrl+e", "export", "Export DM", show=True),
    ]

    DEFAULT_CSS = """
    DmScreen {
        align: center middle;
    }
    #dm-dialog {
        width: 85%;
        height: 85%;
        border: thick $accent;
        background: $surface;
        layout: vertical;
    }
    #dm-menubar {
        height: 3;
        background: $panel;
        border-bottom: solid $accent;
        padding: 0 2;
        layout: horizontal;
        align: left middle;
    }
    #dm-menu-title {
        width: 1fr;
        text-style: bold;
    }
    #dm-menu-actions {
        width: auto;
        color: $text-muted;
    }
    #dm-close {
        width: 3;
        min-width: 3;
        height: 1;
        min-height: 1;
        border: none;
        background: transparent;
        padding: 0 0;
        color: $text-muted;
        margin-left: 1;
    }
    #dm-close:hover {
        color: $text;
        background: $boost;
    }
    VerticalScroll {
        height: 1fr;
        padding: 1 1 0 1;
        scrollbar-size-vertical: 0;
    }
    #dm-input-row {
        height: 3;
        border-top: solid $panel;
        layout: horizontal;
        align: left middle;
        padding: 0 1;
    }
    #dm-prompt {
        width: auto;
        color: $text-muted;
    }
    #dm-input {
        width: 1fr;
        border: none;
        background: transparent;
    }
    """

    def __init__(self, agent: Agent):
        super().__init__()
        self._agent = agent

    def compose(self) -> ComposeResult:
        colour = colour_for(self._agent.name)
        with Vertical(id="dm-dialog"):
            with Horizontal(id="dm-menubar"):
                yield Label(
                    f"Chat with [{colour} bold]{self._agent.name}[/]",
                    id="dm-menu-title",
                )
                yield Label("[dim]Ctrl+E export  Esc close[/]", id="dm-menu-actions")
                yield Button("✕", id="dm-close", variant="default")
            yield VerticalScroll(id="dm-scroll")
            with Horizontal(id="dm-input-row"):
                yield Label("> ", id="dm-prompt")
                yield Input(placeholder=f"Message {self._agent.name}…", id="dm-input")

    def on_mount(self) -> None:
        scroll = self.query_one("#dm-scroll", VerticalScroll)
        if self._agent.dm_history:
            for entry in self._agent.dm_history:
                from_me = entry["role"] == "user"
                scroll.mount(MessageBubble(
                    "You" if from_me else self._agent.name,
                    entry["text"],
                    from_me=from_me,
                ))
            scroll.scroll_end(animate=False)
        else:
            scroll.mount(SystemMessage(f"Start a private conversation with {self._agent.name}."))
        self.query_one("#dm-input", Input).focus()

    def append_agent_message(self, text: str) -> None:
        scroll = self.query_one("#dm-scroll", VerticalScroll)
        scroll.mount(MessageBubble(self._agent.name, text))
        scroll.scroll_end(animate=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        if text.startswith("/run "):
            args = text[5:].strip()
            if args:
                self.app._run_claude_interactive(args)
            return

        scroll = self.query_one("#dm-scroll", VerticalScroll)
        scroll.mount(MessageBubble("You", text, from_me=True))
        scroll.scroll_end(animate=False)
        asyncio.create_task(self._agent.send(text, from_dm=True))

    def action_export(self) -> None:
        lines = []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines.append(f"DM with {self._agent.name}  —  exported {ts}")
        lines.append("=" * 50)
        for entry in self._agent.dm_history:
            who = "You" if entry["role"] == "user" else self._agent.name
            lines.append(f"\n{who}:\n{entry['text']}")
        out = logger.export_chat(lines)
        scroll = self.query_one("#dm-scroll", VerticalScroll)
        scroll.mount(SystemMessage(f"Exported → {out}"))
        scroll.scroll_end(animate=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dm-close":
            self.dismiss()
