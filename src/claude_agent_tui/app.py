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
        self.sub_title = "Ctrl+N: new agent  |  @Name or @everyone to address  |  Enter on agent: DM  |  Ctrl+D: raw log"
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

        if text.startswith("/task "):
            description = text[6:].strip()
            if description:
                self._enqueue_task(description)
            return

        chat.post_message_line("You", text, colour="white")

        segments = self._split_for_agents(text)
        for name, segment in segments.items():
            self._last_active_agent = name
            asyncio.create_task(self._agents[name].send(segment))

    def on_compose_bar_mention_query(self, event: ComposeBar.MentionQuery) -> None:
        prefix = event.prefix.lower()
        matches = [name for name in self._agents if name.lower().startswith(prefix)]
        self.query_one(ComposeBar).set_suggestions(matches)

    _AT_RE = re.compile(r'@(\w+)', re.IGNORECASE)

    def _split_for_agents(self, text: str) -> dict[str, str]:
        """Route by @mentions.

        @everyone  → full text to all agents
        @Name      → segment to that agent (case-insensitive)
        no mention → full text to next available agent
        """
        name_map = {n.lower(): n for n in self._agents}
        all_matches = list(self._AT_RE.finditer(text))

        # @everyone → broadcast
        if any(m.group(1).lower() == 'everyone' for m in all_matches):
            return {name: text for name in self._agents}

        # Valid @agent mentions only
        valid = [
            (m.start(), name_map[m.group(1).lower()], len(m.group(0)))
            for m in all_matches
            if m.group(1).lower() in name_map
        ]

        if not valid:
            target = self._next_available_agent()
            return {target: text} if target else {}

        if len(valid) == 1:
            pos, name, mlen = valid[0]
            segment = text[pos + mlen:].lstrip(' ,:;').strip()
            return {name: segment or text}

        # Multiple @mentions — split text at each mention boundary
        valid.sort()
        segments: dict[str, str] = {}
        for i, (pos, name, mlen) in enumerate(valid):
            end = valid[i + 1][0] if i + 1 < len(valid) else len(text)
            segment = text[pos + mlen:end].lstrip(' ,:;').strip()
            segments[name] = segment
        return segments

    def _next_available_agent(self) -> str | None:
        """Return the best agent to receive an unaddressed message."""
        last = self._last_active_agent
        # Prefer last active if they're free
        if last in self._agents and self._agents[last].status in (AgentStatus.IDLE, AgentStatus.DONE):
            return last
        for name, a in self._agents.items():
            if a.status == AgentStatus.IDLE:
                return name
        for name, a in self._agents.items():
            if a.status == AgentStatus.DONE:
                return name
        # All busy — still route to last active or first
        if last in self._agents:
            return last
        return next(iter(self._agents), None)

    def _enqueue_task(self, description: str) -> None:
        task = bus_mod.add_task(description)
        chat = self.query_one(ChatView)
        chat.post_system(f"📋 Task queued [{task.id}]: {description}")
        # Nudge exactly one free agent — when they finish the STATUS handler chains to the next task
        free = next(
            (a for a in self._agents.values()
             if a.status in (AgentStatus.IDLE, AgentStatus.DONE)
             or (a.status == AgentStatus.BLOCKED and "waiting" in a.status_text.lower())),
            None,
        )
        if free:
            asyncio.create_task(
                free.send("New task queued. Silently call claim_task() and pick it up — no need to announce you're checking.")
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
            self.query_one(ComposeBar).set_suggestions([])

    def action_export_chat(self) -> None:
        chat = self.query_one(ChatView)
        out = logger.export_chat(chat.plain_lines)
        chat.post_system(f"Chat exported → {out}")

    async def on_unmount(self) -> None:
        if self._bus_worker:
            self._bus_worker.cancel()
