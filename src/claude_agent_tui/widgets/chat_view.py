"""Main team chat display — messenger-style bubble layout."""

from __future__ import annotations

import re

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget

from ..names import colour_for
from .bubble import MessageBubble, PermissionBubble, SystemMessage

_MARKUP_RE = re.compile(r"\[/?[^\]]+\]")


def _strip_markup(text: str) -> str:
    return _MARKUP_RE.sub("", text)


class ChatView(Widget):
    DEFAULT_CSS = """
    ChatView {
        height: 1fr;
    }
    VerticalScroll {
        height: 1fr;
        padding: 1 1 0 1;
        scrollbar-size-vertical: 0;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._plain_lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-scroll")

    def _scroll(self) -> VerticalScroll:
        return self.query_one(VerticalScroll)

    def _append(self, widget: Widget) -> None:
        self._scroll().mount(widget)
        self._scroll().scroll_end(animate=False)

    def post_message_line(
        self,
        sender: str,
        text: str,
        *,
        colour: str | None = None,
        style: str = "",
        variant: str = "",
    ) -> None:
        from_me = sender == "You"
        bubble = MessageBubble(sender, text, from_me=from_me, variant=variant)
        self._append(bubble)
        self._plain_lines.append(
            f"{'→' if from_me else '←'} {sender}: {_strip_markup(text)}"
        )

    def post_system(self, text: str) -> None:
        self._append(SystemMessage(text))
        self._plain_lines.append(f"  * {text}")

    def post_permission(self, agent_name: str, tool_name: str, tool_input: dict) -> None:
        self._append(PermissionBubble(agent_name, tool_name, tool_input))

    def post_ask(self, agent_name: str, question: str) -> None:
        bubble = MessageBubble(agent_name, f"❓ {question}", is_question=True)
        self._append(bubble)
        self._plain_lines.append(f"← {agent_name} [?]: {question}")

    @property
    def plain_lines(self) -> list[str]:
        return list(self._plain_lines)
