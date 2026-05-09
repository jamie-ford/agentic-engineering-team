"""Shared async event bus connecting MCP server → TUI."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent


class EventType(str, Enum):
    MESSAGE = "message"        # agent → main team chat (via send_message tool)
    STATUS = "status"          # agent status changed (working/idle/done/blocked)
    STATUS_UPDATE = "status_update"  # agent updated their one-line summary text
    ASK_USER = "ask_user"      # agent needs human reply (blocks agent loop)
    DM_REPLY = "dm_reply"      # agent produced text output → shown in DM view
    AGENT_ADDED = "agent_added"
    AGENT_REMOVED = "agent_removed"
    PERMISSION_REQUEST = "permission_request"  # agent needs tool permission


@dataclass
class BusEvent:
    type: EventType
    agent_name: str
    payload: dict = field(default_factory=dict)


_queue: asyncio.Queue[BusEvent] = asyncio.Queue()

# Futures awaiting user replies: agent_name → Future[str]
_pending_replies: dict[str, asyncio.Future[str]] = {}

# Live agent registry — populated by app.py, read by mcp_server.py
_agents: dict[str, "Agent"] = {}


def get_queue() -> asyncio.Queue[BusEvent]:
    return _queue


async def post(event: BusEvent) -> None:
    await _queue.put(event)


def register_agent(name: str, agent: "Agent") -> None:
    _agents[name] = agent


def unregister_agent(name: str) -> None:
    _agents.pop(name, None)


def get_agents() -> dict[str, "Agent"]:
    return _agents


def register_ask(agent_name: str, future: asyncio.Future[str]) -> None:
    _pending_replies[agent_name] = future


def resolve_ask(agent_name: str, reply: str) -> bool:
    fut = _pending_replies.pop(agent_name, None)
    if fut and not fut.done():
        fut.set_result(reply)
        return True
    return False


def has_pending_ask(agent_name: str) -> bool:
    return agent_name in _pending_replies


# --- Permission system ---

# Pending permission futures: agent_name → Future[str]
_pending_permissions: dict[str, asyncio.Future[str]] = {}

# Tools allowed for the lifetime of the session: {(agent_name, tool_name)}
_session_allowed: set[tuple[str, str]] = set()


def register_permission(agent_name: str, future: asyncio.Future[str]) -> None:
    _pending_permissions[agent_name] = future


def resolve_permission(agent_name: str, decision: str) -> bool:
    fut = _pending_permissions.pop(agent_name, None)
    if fut and not fut.done():
        fut.set_result(decision)
        return True
    return False


def allow_session(agent_name: str, tool_name: str) -> None:
    _session_allowed.add((agent_name, tool_name))


def is_session_allowed(agent_name: str, tool_name: str) -> bool:
    return (agent_name, tool_name) in _session_allowed


# --- Task queue ---

@dataclass
class Task:
    id: str
    description: str
    added_at: str


_task_queue: list[Task] = []


def add_task(description: str) -> Task:
    task = Task(
        id=uuid.uuid4().hex[:6],
        description=description,
        added_at=datetime.now().strftime("%H:%M"),
    )
    _task_queue.append(task)
    return task


def claim_task() -> Task | None:
    return _task_queue.pop(0) if _task_queue else None


def peek_tasks() -> list[Task]:
    return list(_task_queue)
