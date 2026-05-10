"""Bottom input bar with @mention autocomplete."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label


class MentionChip(Widget):
    """Clickable @Name chip shown in the autocomplete bar."""

    DEFAULT_CSS = """
    MentionChip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        background: $panel;
        color: $accent;
    }
    MentionChip:hover {
        background: $accent;
        color: $background;
    }
    """

    class Selected(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def render(self) -> str:
        return f"@{self._name}"

    def on_click(self) -> None:
        self.post_message(self.Selected(self._name))


class ComposeBar(Widget):
    DEFAULT_CSS = """
    ComposeBar {
        height: auto;
        border-top: solid $panel;
        layout: vertical;
    }
    #suggestions {
        height: 1;
        layout: horizontal;
        display: none;
        background: $boost;
        padding: 0 1;
    }
    #suggestions.active {
        display: block;
    }
    #input-row {
        height: 3;
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

    class MentionQuery(Message):
        """Posted when user is typing an @mention; prefix is the text typed after @."""
        def __init__(self, prefix: str) -> None:
            super().__init__()
            self.prefix = prefix

    def compose(self) -> ComposeResult:
        yield Horizontal(id="suggestions")
        with Horizontal(id="input-row"):
            yield Label("> ", id="prompt-label")
            yield Input(
                placeholder="Type a message… (@Name to address, @everyone to broadcast)",
                id="message-input",
            )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.set_suggestions([])
            self.post_message(self.Submitted(text))
            event.input.clear()

    def on_input_changed(self, event: Input.Changed) -> None:
        prefix = self._mention_prefix(event.value, event.input.cursor_position)
        if prefix is not None:
            self.post_message(self.MentionQuery(prefix))
        else:
            self.set_suggestions([])

    def on_mention_chip_selected(self, event: MentionChip.Selected) -> None:
        self._complete(event.name)

    # --- mention detection & completion ---

    def _mention_prefix(self, value: str, cursor: int) -> str | None:
        """Return the text after the last @ before the cursor, or None if not in a mention."""
        before = value[:cursor]
        at = before.rfind("@")
        if at == -1:
            return None
        # Skip if @ follows an alphanumeric (e.g. email address)
        if at > 0 and before[at - 1].isalnum():
            return None
        word = before[at + 1:]
        # A space after @ means we've left the mention
        if " " in word or "\t" in word:
            return None
        return word  # may be "" if cursor is right after @

    def _complete(self, name: str) -> None:
        """Replace the current @-prefix with @name and a trailing space."""
        inp = self.query_one(Input)
        cursor = inp.cursor_position
        value = inp.value
        before = value[:cursor]
        after = value[cursor:]

        at = before.rfind("@")
        if at == -1:
            return

        new_before = before[:at] + f"@{name} "
        inp.value = new_before + after
        self.set_suggestions([])
        inp.focus()

    # --- public API ---

    def set_suggestions(self, names: list[str]) -> None:
        """Show or hide the autocomplete bar. Call with [] to hide."""
        container = self.query_one("#suggestions")
        container.remove_children()
        if names:
            for name in names:
                container.mount(MentionChip(name))
            container.add_class("active")
        else:
            container.remove_class("active")

    def focus_input(self) -> None:
        self.query_one(Input).focus()
