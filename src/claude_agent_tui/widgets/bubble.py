"""Messenger-style message bubbles using Rich Panel for rounded borders."""

from __future__ import annotations

from datetime import datetime

from rich.box import ROUNDED
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
import json

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from ..names import colour_for

# Border colours per message type
_BORDER = {
    "agent": "grey50",
    "me": "steel_blue",
    "done": "green",
    "blocked": "red",
    "question": "yellow",
    "system": "grey30",
}


def _panel(
    body: str,
    title: str,
    *,
    title_align: str = "left",
    border_colour: str = "grey50",
) -> Panel:
    return Panel(
        Text.from_markup(escape(body)),
        title=title,
        title_align=title_align,
        border_style=border_colour,
        box=ROUNDED,
        padding=(0, 1),
        expand=True,
    )


class MessageBubble(Widget):
    """A chat bubble — left-aligned for others, right-aligned for 'You'."""

    DEFAULT_CSS = """
    MessageBubble {
        height: auto;
        width: 100%;
        layout: horizontal;
        margin-bottom: 1;
    }
    MessageBubble .bubble {
        height: auto;
        width: 72%;
    }
    MessageBubble .spacer {
        width: 28%;
        height: 1;
    }
    """

    def __init__(
        self,
        sender: str,
        text: str,
        *,
        from_me: bool = False,
        is_question: bool = False,
        variant: str = "",  # "done" | "blocked" | ""
    ):
        super().__init__()
        self._sender = sender
        self._text = text
        self._from_me = from_me
        self._is_question = is_question
        self._variant = variant
        self._ts = datetime.now().strftime("%H:%M")

    def compose(self) -> ComposeResult:
        rich_panel = self._make_panel()
        bubble = Static(rich_panel, classes="bubble")
        spacer = Static("", classes="spacer")
        if self._from_me:
            yield spacer
            yield bubble
        else:
            yield bubble
            yield spacer

    def _make_panel(self) -> Panel:
        ts = self._ts

        if self._from_me:
            title = f"[bold white]You[/]  [dim]{ts}[/]"
            border_colour = _BORDER["me"]
            title_align = "right"
        else:
            colour = colour_for(self._sender)
            title = f"[{colour} bold]{self._sender}[/]  [dim]{ts}[/]"
            border_colour = _BORDER["agent"]
            title_align = "left"

        if self._variant == "done":
            border_colour = _BORDER["done"]
        elif self._variant == "blocked":
            border_colour = _BORDER["blocked"]
        elif self._is_question:
            border_colour = _BORDER["question"]

        return _panel(
            self._text,
            title,
            title_align=title_align,
            border_colour=border_colour,
        )


class PermissionBubble(Widget):
    """Inline permission request with Allow / Allow Session / Deny buttons."""

    class Decision(Message):
        def __init__(self, agent_name: str, tool_name: str, decision: str):
            super().__init__()
            self.agent_name = agent_name
            self.tool_name = tool_name
            self.decision = decision

    DEFAULT_CSS = """
    PermissionBubble {
        height: auto;
        width: 100%;
        border: solid yellow;
        border-title-color: yellow;
        padding: 0 1;
        margin-bottom: 1;
        background: $surface;
    }
    PermissionBubble #perm-detail {
        height: auto;
        padding: 0 0 1 0;
        color: $text-muted;
    }
    PermissionBubble #perm-actions {
        height: 3;
        layout: horizontal;
        align: left middle;
    }
    PermissionBubble #perm-allow {
        margin-right: 1;
        background: $success;
        border: none;
        min-width: 13;
    }
    PermissionBubble #perm-session {
        margin-right: 1;
        background: $primary;
        border: none;
        min-width: 15;
    }
    PermissionBubble #perm-deny {
        background: $error;
        border: none;
        min-width: 7;
    }
    """

    def __init__(self, agent_name: str, tool_name: str, tool_input: dict):
        super().__init__()
        self._agent_name = agent_name
        self._tool_name = tool_name
        self._tool_input = tool_input
        colour = colour_for(agent_name)
        self.border_title = f"🔐 [{colour} bold]{agent_name}[/] needs permission"

    def compose(self) -> ComposeResult:
        detail = self._format_input()
        yield Static(f"[bold]{escape(self._tool_name)}[/]  {detail}", id="perm-detail")
        with Horizontal(id="perm-actions"):
            yield Button("Allow once", id="perm-allow", variant="success")
            yield Button("Allow session", id="perm-session", variant="primary")
            yield Button("Deny", id="perm-deny", variant="error")

    def _format_input(self) -> str:
        inp = self._tool_input
        if not inp:
            return ""
        # Show the most useful field for common tools
        for key in ("command", "cmd", "file_path", "path", "pattern", "query"):
            if key in inp:
                return f"[dim]{escape(str(inp[key])[:120])}[/]"
        # Fallback: compact JSON
        try:
            raw = json.dumps(inp, separators=(",", ":"))
            return f"[dim]{escape(raw[:120])}[/]"
        except Exception:
            return ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        decision_map = {
            "perm-allow": "allow",
            "perm-session": "allow_session",
            "perm-deny": "deny",
        }
        decision = decision_map.get(event.button.id or "")
        if not decision:
            return
        self.post_message(self.Decision(self._agent_name, self._tool_name, decision))
        # Dim the bubble to show it's been resolved
        for btn in self.query(Button):
            btn.disabled = True
        self.border_title = (
            f"🔐 {self._agent_name} — [dim]{decision.replace('_', ' ')}[/]"
        )


class SystemMessage(Widget):
    """Centred dim line for join/leave/export notices."""

    DEFAULT_CSS = """
    SystemMessage {
        height: auto;
        width: 100%;
        padding: 0 0 1 0;
        text-align: center;
    }
    SystemMessage Static {
        width: 100%;
        text-align: center;
    }
    """

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static(f"[dim]— {escape(self._text)} —[/]")
