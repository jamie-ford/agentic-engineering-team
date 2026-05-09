"""Full-screen modal showing an agent's raw stream-json log."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import RichLog, Label, Button
from textual.containers import Vertical, Horizontal

from ..agent import Agent
from ..names import colour_for


class AgentDetailModal(ModalScreen):
    DEFAULT_CSS = """
    AgentDetailModal {
        align: center middle;
    }
    #dialog {
        width: 90%;
        height: 85%;
        border: thick $accent;
        background: $surface;
        padding: 0 1;
    }
    #title-bar {
        height: 3;
        align: left middle;
        padding: 0 1;
        border-bottom: solid $panel;
    }
    #close-btn {
        dock: right;
    }
    RichLog {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, agent: Agent):
        super().__init__()
        self._agent = agent

    def compose(self) -> ComposeResult:
        name = self._agent.name
        colour = colour_for(name)
        with Vertical(id="dialog"):
            with Horizontal(id="title-bar"):
                yield Label(f"[{colour} bold]{name}[/] — full conversation log")
                yield Button("✕ Close", id="close-btn", variant="default")
            yield RichLog(highlight=True, markup=True, wrap=True, id="detail-log")

    def on_mount(self) -> None:
        log = self.query_one(RichLog)
        events = self._agent.stream_log
        if not events:
            log.write("[dim]No output yet.[/]")
            return
        for event in events:
            self._render_event(log, event)

    def _render_event(self, log: RichLog, event: dict) -> None:
        etype = event.get("type", "")
        subtype = event.get("subtype", "")

        if etype == "system" and subtype == "init":
            sid = event.get("session_id", "?")
            log.write(f"[dim]── session {sid} ──[/]")

        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    log.write(f"[cyan]{block['text']}[/]")
                elif block.get("type") == "tool_use":
                    tool = block.get("name", "?")
                    inp = block.get("input", {})
                    log.write(f"[yellow]▶ tool_use:[/] [bold]{tool}[/]  {inp}")

        elif etype == "tool":
            content = event.get("content", [])
            for c in content:
                text = c.get("text", "") if isinstance(c, dict) else str(c)
                log.write(f"[green]◀ tool_result:[/] {text[:300]}")

        elif etype == "result":
            cost = event.get("cost_usd")
            turns = event.get("num_turns")
            parts = []
            if turns is not None:
                parts.append(f"{turns} turns")
            if cost is not None:
                parts.append(f"${cost:.4f}")
            log.write(f"[dim]── done ({', '.join(parts)}) ──[/]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss()
