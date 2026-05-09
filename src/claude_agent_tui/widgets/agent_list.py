"""Left sidebar: list of agents with status indicators and summary text."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ListItem, ListView, Label

from ..agent import Agent, AgentStatus
from ..names import colour_for

STATUS_ICON = {
    AgentStatus.IDLE: ("💤", "dim"),
    AgentStatus.WORKING: ("⚙", "cyan"),
    AgentStatus.BLOCKED: ("⚠", "yellow"),
    AgentStatus.DONE: ("✓", "green"),
}

_SIDEBAR_WIDTH = 36


class AgentListWidget(Widget):
    class AgentSelected(Message):
        """Fired when an agent is chosen (Enter/click) → open DM."""
        def __init__(self, name: str):
            super().__init__()
            self.name = name

    DEFAULT_CSS = f"""
    AgentListWidget {{
        width: {_SIDEBAR_WIDTH};
        border-right: solid $panel;
    }}
    AgentListWidget > Label {{
        padding: 0 1;
        color: $text-muted;
        text-style: bold;
    }}
    ListView {{
        background: transparent;
    }}
    ListItem {{
        padding: 0 1;
        height: auto;
    }}
    ListItem:hover {{
        background: $boost;
    }}
    ListItem.--highlight {{
        background: $accent 20%;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Label("  Agents")
        yield ListView(id="agent-listview")

    def add_agent(self, agent: Agent) -> None:
        lv = self.query_one(ListView)
        item = ListItem(Label(self._label(agent)), id=f"agent-{agent.name}")
        item.data = agent.name  # type: ignore[attr-defined]
        lv.append(item)

    def remove_agent(self, name: str) -> None:
        try:
            item = self.query_one(f"#agent-{name}", ListItem)
            item.remove()
        except Exception:
            pass

    def refresh_agent(self, agent: Agent) -> None:
        try:
            item = self.query_one(f"#agent-{agent.name}", ListItem)
            item.query_one(Label).update(self._label(agent))
        except Exception:
            pass

    def _label(self, agent: Agent) -> str:
        icon, style = STATUS_ICON.get(agent.status, ("?", "white"))
        colour = colour_for(agent.name)
        name_part = f"[{colour} bold]{agent.name}[/]"
        icon_part = f"[{style}]{icon}[/]"

        if agent.status_text:
            # Truncate summary to fit sidebar
            max_summary = _SIDEBAR_WIDTH - len(agent.name) - 5
            summary = agent.status_text[:max_summary]
            if len(agent.status_text) > max_summary:
                summary = summary[:-1] + "…"
            return f"{name_part} {icon_part}\n  [dim]{summary}[/]"
        return f"{name_part} {icon_part}"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter key on a highlighted item → open DM."""
        name = getattr(event.item, "data", None)
        if name:
            self.post_message(self.AgentSelected(name=name))

    @property
    def selected_name(self) -> str | None:
        lv = self.query_one(ListView)
        if lv.highlighted_child is None:
            return None
        return getattr(lv.highlighted_child, "data", None)
