"""Local MCP server giving agents team-communication tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import logging

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.requests import Request
import uvicorn

# Silence uvicorn loggers so errors don't bleed into the TUI terminal
for _log in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
    logging.getLogger(_log).setLevel(logging.CRITICAL)

from . import bus as bus_mod
from .bus import BusEvent, EventType

PORT = 18765
MCP_CONFIG_PATH = Path("/tmp/claude-tui-mcp.json")


def write_mcp_config() -> None:
    config = {
        "mcpServers": {
            "team-chat": {
                "type": "sse",
                "url": f"http://localhost:{PORT}/sse",
            }
        }
    }
    MCP_CONFIG_PATH.write_text(json.dumps(config, indent=2))


_TOOLS = [
    Tool(
        name="claim_task",
        description=(
            "Claim the next task from the shared team queue. "
            "Returns the task description so you can start work on it, or tells you the queue is empty. "
            "Call this when you finish your current work and want something new to do."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="list_tasks",
        description="Preview the team task queue without claiming anything. Use this to see what's waiting.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="list_agents",
        description=(
            "List all other team members currently online, with their name, status, and what they're working on. "
            "Use this to find out who can help you or who to delegate to."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="message_agent",
        description=(
            "Send a direct message to another team member. "
            "Their reply will appear in team chat. Use this to ask for help, share context, or coordinate work."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "description": "Name of the team member to message"},
                "message": {"type": "string", "description": "Your message to them"},
            },
            "required": ["agent_name", "message"],
        },
    ),
    Tool(
        name="set_working_directory",
        description=(
            "Set your working directory to a specific repo or folder. "
            "Takes effect from your next turn — the repo's CLAUDE.md will be loaded automatically. "
            "Call this before starting work on a specific project."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the directory, e.g. /code/repo1"},
            },
            "required": ["path"],
        },
    ),
    Tool(
        name="send_message",
        description=(
            "Post an update to the shared team chat visible to everyone. "
            "Use for announcements, progress updates, and milestone reports."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="ask_user",
        description=(
            "Ask the human a direct question in the team chat. "
            "Pauses your work until they reply. Use when you need clarification or approval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="mark_done",
        description="Signal that your task is complete. Posts a summary to the team chat and marks you as done.",
        inputSchema={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One or two sentences on what was accomplished"},
            },
            "required": ["summary"],
        },
    ),
    Tool(
        name="mark_blocked",
        description="Signal that you are blocked and cannot continue without human input. Posts reason to team chat.",
        inputSchema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why you are blocked"},
            },
            "required": ["reason"],
        },
    ),
    Tool(
        name="update_status",
        description=(
            "Update the short status summary shown next to your name in the sidebar. "
            "Call this whenever you start a new piece of work. Keep it under 40 characters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Short phrase describing current work, e.g. 'Upgrading dependencies'"},
            },
            "required": ["summary"],
        },
    ),
]


def _make_server(agent_name: str) -> Server:
    server = Server("team-chat")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "claim_task":
            task = bus_mod.claim_task()
            if task is None:
                return [TextContent(type="text", text="No tasks in the queue right now.")]
            return [TextContent(type="text", text=f"Task claimed: {task.description}")]

        elif name == "list_tasks":
            tasks = bus_mod.peek_tasks()
            if not tasks:
                return [TextContent(type="text", text="Task queue is empty.")]
            lines = "\n".join(f"- [{t.id}] {t.description} (added {t.added_at})" for t in tasks)
            return [TextContent(type="text", text=lines)]

        elif name == "list_agents":
            roster = [
                {
                    "name": n,
                    "status": a.status.value,
                    "working_on": a.status_text or "(idle)",
                }
                for n, a in bus_mod.get_agents().items()
                if n != agent_name
            ]
            if not roster:
                return [TextContent(type="text", text="You're the only one on the team right now.")]
            lines = "\n".join(
                f"- {r['name']} [{r['status']}]: {r['working_on']}" for r in roster
            )
            return [TextContent(type="text", text=lines)]

        elif name == "message_agent":
            target_name = arguments["agent_name"]
            message = arguments["message"]
            target = bus_mod.get_agents().get(target_name)
            if not target:
                return [TextContent(type="text", text=f"No agent named '{target_name}' is online. Use list_agents to see who's available.")]
            # Show the inter-agent message in team chat so the user can follow the coordination
            await bus_mod.post(BusEvent(
                type=EventType.MESSAGE,
                agent_name=agent_name,
                payload={"message": f"[dim]→ {target_name}:[/dim] {message}"},
            ))
            asyncio.create_task(target.send(f"{agent_name} says: {message}"))
            return [TextContent(type="text", text=f"Message sent to {target_name}. Their reply will appear in team chat.")]

        elif name == "set_working_directory":
            import os
            path = arguments["path"]
            if not os.path.isdir(path):
                return [TextContent(type="text", text=f"Directory not found: {path}")]
            agent = bus_mod.get_agents().get(agent_name)
            if agent:
                agent.cwd = path
                claude_md = os.path.join(path, "CLAUDE.md")
                note = " (CLAUDE.md found)" if os.path.isfile(claude_md) else " (no CLAUDE.md)"
                return [TextContent(type="text", text=f"Working directory set to {path}{note}. Takes effect next turn.")]
            return [TextContent(type="text", text=f"Working directory set to {path}. Takes effect next turn.")]

        elif name == "send_message":
            await bus_mod.post(BusEvent(
                type=EventType.MESSAGE,
                agent_name=agent_name,
                payload={"message": arguments["message"]},
            ))
            return [TextContent(type="text", text="Message posted to team chat.")]

        elif name == "ask_user":
            question = arguments["question"]
            loop = asyncio.get_event_loop()
            future: asyncio.Future[str] = loop.create_future()
            bus_mod.register_ask(agent_name, future)
            await bus_mod.post(BusEvent(
                type=EventType.ASK_USER,
                agent_name=agent_name,
                payload={"question": question},
            ))
            reply = await asyncio.wait_for(future, timeout=600)
            return [TextContent(type="text", text=reply)]

        elif name == "mark_done":
            summary = arguments["summary"]
            await bus_mod.post(BusEvent(
                type=EventType.STATUS,
                agent_name=agent_name,
                payload={"status": "done", "message": summary},
            ))
            return [TextContent(type="text", text="Marked as done.")]

        elif name == "mark_blocked":
            reason = arguments["reason"]
            await bus_mod.post(BusEvent(
                type=EventType.STATUS,
                agent_name=agent_name,
                payload={"status": "blocked", "message": reason},
            ))
            return [TextContent(type="text", text="Marked as blocked.")]

        elif name == "update_status":
            summary = arguments["summary"][:60]  # cap length
            await bus_mod.post(BusEvent(
                type=EventType.STATUS_UPDATE,
                agent_name=agent_name,
                payload={"summary": summary},
            ))
            return [TextContent(type="text", text="Status updated.")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _handle_permission(scope, receive, send) -> None:
    """PreToolCall hook endpoint — called by per-agent hook scripts, blocks until TUI resolves."""
    from urllib.parse import parse_qs

    body = b""
    more = True
    while more:
        msg = await receive()
        body += msg.get("body", b"")
        more = msg.get("more_body", False)

    qs = parse_qs(scope.get("query_string", b"").decode())
    agent_name = qs.get("agent", ["Unknown"])[0]

    try:
        data = json.loads(body)
    except Exception:
        data = {}

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input", {})

    if bus_mod.is_session_allowed(agent_name, tool_name):
        decision = "allow"
    else:
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()
        bus_mod.register_permission(agent_name, future)
        await bus_mod.post(BusEvent(
            type=EventType.PERMISSION_REQUEST,
            agent_name=agent_name,
            payload={"tool_name": tool_name, "tool_input": tool_input},
        ))
        try:
            decision = await asyncio.wait_for(future, timeout=300.0)
        except asyncio.TimeoutError:
            decision = "deny"

    if decision == "allow_session":
        bus_mod.allow_session(agent_name, tool_name)
        decision = "allow"

    response_body = json.dumps({"decision": decision}).encode()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(response_body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": response_body})


def _build_asgi_app():
    """Pure ASGI app — bypasses Starlette routing so no 'no response returned' errors."""
    sse = SseServerTransport("/messages/")

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path: str = scope.get("path", "")

        if path == "/sse":
            request = Request(scope, receive, send)
            agent_name = request.query_params.get("agent", "Unknown")
            server = _make_server(agent_name)
            try:
                async with sse.connect_sse(scope, receive, send) as streams:
                    await server.run(
                        streams[0], streams[1], server.create_initialization_options()
                    )
            except Exception:
                pass  # client disconnected or protocol error — swallow silently

        elif path.startswith("/messages"):
            await sse.handle_post_message(scope, receive, send)

        elif path == "/permission":
            await _handle_permission(scope, receive, send)

        else:
            # 404 for anything else
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    return app


async def start_server() -> None:
    write_mcp_config()
    config = uvicorn.Config(
        _build_asgi_app(),
        host="127.0.0.1",
        port=PORT,
        log_level="critical",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()
