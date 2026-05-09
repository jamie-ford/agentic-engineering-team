"""Main Textual application."""

from __future__ import annotations

import asyncio
import re

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from .agent import Agent, AgentStatus
from . import bus as bus_mod
from . import logger
from .bus import BusEvent, EventType
from .names import next_name, release_name, colour_for
from .widgets.agent_list import AgentListWidget
from .widgets.agent_detail import AgentDetailModal
from .widgets.chat_view import ChatView
from .widgets.compose_bar import ComposeBar
from .widgets.bubble import PermissionBubble
from .widgets.dm_screen import DmScreen


class AgentTuiApp(App):
    TITLE = "Claude Agent Team"
    BINDINGS = [
        Binding("ctrl+n", "new_agent", "New agent", show=True),
        Binding("ctrl+d", "agent_detail", "Raw detail", show=True),
        Binding("ctrl+k", "kill_agent", "Kill agent", show=True),
        Binding("ctrl+e", "export_chat", "Export chat", show=True),
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]
    CSS = """
    Screen {
        layout: vertical;
    }
    #main-row {
        height: 1fr;
        layout: horizontal;
    }
    #chat-col {
        width: 1fr;
        layout: vertical;
    }
    """

    def __init__(self):
        super().__init__()
        self._agents: dict[str, Agent] = {}
        self._bus_worker: asyncio.Task | None = None
        self._dm_screens: dict[str, DmScreen] = {}
        self._last_active_agent: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-row"):
            yield AgentListWidget(id="agent-list")
            with Vertical(id="chat-col"):
                yield ChatView(id="chat-view")
        yield ComposeBar(id="compose-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Ctrl+N: new agent  |  Enter on agent: DM  |  Ctrl+D: raw log  |  Ctrl+E: export"
        self.query_one(ComposeBar).focus_input()
        self._bus_worker = asyncio.create_task(self._drain_bus())
        self.query_one(ChatView).post_system(
            "Team chat ready. Ctrl+N to spawn an agent, then assign them a task. "
            "Press Enter on an agent in the sidebar to open a private DM."
        )

    async def _drain_bus(self) -> None:
        q = bus_mod.get_queue()
        while True:
            try:
                event: BusEvent = await q.get()
                self._handle_bus_event(event)
            except Exception as exc:
                logger.crash(exc)
                raise

    def _handle_bus_event(self, event: BusEvent) -> None:
        chat = self.query_one(ChatView)
        agent_list = self.query_one(AgentListWidget)
        agent = self._agents.get(event.agent_name)

        if event.type == EventType.MESSAGE:
            self._last_active_agent = event.agent_name
            # send_message calls are suppressed from main chat — too noisy

        elif event.type == EventType.PERMISSION_REQUEST:
            tool_name = event.payload.get("tool_name", "unknown")
            tool_input = event.payload.get("tool_input", {})
            chat.post_permission(event.agent_name, tool_name, tool_input)

        elif event.type == EventType.ASK_USER:
            question = event.payload["question"]
            chat.post_ask(event.agent_name, question)
            if agent:
                agent.status = AgentStatus.BLOCKED
                agent_list.refresh_agent(agent)

        elif event.type == EventType.STATUS:
            status_str = event.payload.get("status", "")
            msg = event.payload.get("message", "")
            if agent:
                if status_str == "done":
                    agent.status = AgentStatus.DONE
                elif status_str == "blocked":
                    agent.status = AgentStatus.BLOCKED
                elif status_str == "working":
                    agent.status = AgentStatus.WORKING
                elif status_str == "idle":
                    agent.status = AgentStatus.IDLE
                agent_list.refresh_agent(agent)
                # When an agent finishes or goes idle, nudge them if tasks are waiting
                if status_str in ("done", "blocked", "idle") and bus_mod.peek_tasks():
                    asyncio.create_task(
                        agent.send("New task queued. Silently call claim_task() and pick it up — no need to announce you're checking.")
                    )
            if msg:
                if status_str == "done":
                    chat.post_message_line(event.agent_name, f"✓ {msg}", variant="done")
                elif status_str == "blocked":
                    chat.post_message_line(event.agent_name, f"⚠ {msg}", variant="blocked")
                # working/idle transitions are silent in the main chat

        elif event.type == EventType.STATUS_UPDATE:
            summary = event.payload.get("summary", "")
            if agent:
                agent.status_text = summary
                agent_list.refresh_agent(agent)

        elif event.type == EventType.DM_REPLY:
            text = event.payload.get("text", "")
            from_dm = event.payload.get("from_dm", False)
            dm = self._dm_screens.get(event.agent_name)
            if from_dm:
                if dm and text:
                    dm.append_agent_message(text)
            else:
                if text:
                    self._last_active_agent = event.agent_name
                    # Agent text responses suppressed from main chat — use DM screen

        elif event.type == EventType.AGENT_ADDED:
            if agent:
                agent_list.add_agent(agent)
                chat.post_system(f"{event.agent_name} joined the team. 👋")
                if bus_mod.peek_tasks():
                    asyncio.create_task(
                        agent.send("New task queued. Silently call claim_task() and pick it up — no need to announce you're checking.")
                    )

    def on_permission_bubble_decision(self, event: PermissionBubble.Decision) -> None:
        bus_mod.resolve_permission(event.agent_name, event.decision)

    # --- Sidebar agent click → open DM ---

    def on_agent_list_widget_agent_selected(self, event: AgentListWidget.AgentSelected) -> None:
        name = event.name
        if name not in self._agents:
            return
        agent = self._agents[name]

        # If already open, just bring it to focus (dismiss + reopen)
        if name in self._dm_screens:
            # Screen is already on the stack; Textual will focus it
            return

        dm = DmScreen(agent)
        self._dm_screens[name] = dm

        def _on_dm_dismiss(_):
            self._dm_screens.pop(name, None)
            self.query_one(ComposeBar).focus_input()

        self.push_screen(dm, _on_dm_dismiss)

    # --- Main chat input routing ---

    def on_compose_bar_submitted(self, event: ComposeBar.Submitted) -> None:
        text = event.text
        chat = self.query_one(ChatView)
        agent_list = self.query_one(AgentListWidget)

        if text.startswith("/task "):
            description = text[6:].strip()
            if description:
                self._enqueue_task(description)
            return

        chat.post_message_line("You", text, colour="white")

        segments = self._split_for_agents(text, agent_list.selected_name)
        if segments:
            for name, segment in segments.items():
                self._last_active_agent = name
                asyncio.create_task(self._agents[name].send(segment))
        elif self._agents:
            # No target inferred — broadcast to everyone
            for name, agent in self._agents.items():
                asyncio.create_task(agent.send(text))

    # Words that appear as connectors between agent instructions and should
    # be stripped from the trailing edge of each segment.
    _CONNECTOR_RE = re.compile(
        r"[\s\-–—,;]*\b(also|and|but|then|too|additionally|plus)\b[\s\-–—,;]*$",
        re.IGNORECASE,
    )

    def _split_for_agents(self, text: str, selected: str | None) -> dict[str, str]:
        """Return {agent_name: their_segment} for every agent named in the message.

        For a single agent or fallback, the whole message is their segment.
        For multiple agents, the message is split at each name boundary so
        each agent only sees the part addressed to them.
        """
        # Find agent names that are used as direct addressees (at start, or after a separator).
        # Names that appear mid-sentence (e.g. "ask Cleo about X") are subjects, not recipients.
        lower = text.lower()
        positions: list[tuple[int, str]] = []
        for name in self._agents:
            idx = lower.find(name.lower())
            if idx == -1:
                continue
            if idx == 0:
                positions.append((idx, name))
            else:
                prefix = text[:idx].rstrip()
                if prefix and prefix[-1] in "-–—,;|\n":
                    positions.append((idx, name))

        if not positions:
            # No names found — fall back to last active agent, then sidebar selection
            fallback = self._last_active_agent or selected
            if fallback and fallback in self._agents:
                return {fallback: text}
            return {}

        if len(positions) == 1:
            return {positions[0][1]: text}

        # Multiple agents — split at each name occurrence, sorted by position
        positions.sort()
        segments: dict[str, str] = {}
        for i, (pos, name) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            raw = text[pos:end]
            # Strip the agent's own name from the start of their segment
            if raw.lower().startswith(name.lower()):
                raw = raw[len(name):].lstrip(" ,:;-–—")
            # Strip trailing connector words that belong to the next instruction
            raw = self._CONNECTOR_RE.sub("", raw)
            segments[name] = raw.strip(" -–—,;")
        return segments

    def _enqueue_task(self, description: str) -> None:
        task = bus_mod.add_task(description)
        chat = self.query_one(ChatView)
        chat.post_system(f"📋 Task queued [{task.id}]: {description}")
        # Nudge any agent that's idle or waiting for work
        free = [
            a for a in self._agents.values()
            if a.status in (AgentStatus.IDLE, AgentStatus.DONE)
            or (a.status == AgentStatus.BLOCKED and "waiting" in a.status_text.lower())
        ]
        for agent in free:
            asyncio.create_task(
                agent.send("New task queued. Silently call claim_task() and pick it up — no need to announce you're checking.")
            )

    # --- Actions ---

    def action_new_agent(self) -> None:
        name = next_name()
        agent = Agent(name)
        self._agents[name] = agent
        bus_mod.register_agent(name, agent)
        asyncio.create_task(
            bus_mod.post(BusEvent(type=EventType.AGENT_ADDED, agent_name=name))
        )

    def action_agent_detail(self) -> None:
        agent_list = self.query_one(AgentListWidget)
        name = agent_list.selected_name
        if name and name in self._agents:
            self.push_screen(AgentDetailModal(self._agents[name]))

    def action_kill_agent(self) -> None:
        agent_list = self.query_one(AgentListWidget)
        name = agent_list.selected_name
        if name and name in self._agents:
            self._agents[name].kill()
            del self._agents[name]
            bus_mod.unregister_agent(name)
            release_name(name)
            agent_list.remove_agent(name)
            self._dm_screens.pop(name, None)
            if self._last_active_agent == name:
                self._last_active_agent = None
            self.query_one(ChatView).post_system(f"{name} left the team. 😢")

    def action_export_chat(self) -> None:
        chat = self.query_one(ChatView)
        out = logger.export_chat(chat.plain_lines)
        chat.post_system(f"Chat exported → {out}")

    async def on_unmount(self) -> None:
        if self._bus_worker:
            self._bus_worker.cancel()
