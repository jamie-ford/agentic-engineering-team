# Claude Agent TUI

A terminal UI that wraps the `claude` CLI to simulate a named software engineering team. Multiple agents run as independent Claude Code subprocess sessions, post updates to a shared team chat, and can collaborate with each other.

## Running

```bash
./run.sh
```

Requires `claude` CLI in PATH with a valid login. No API key needed — inherits the user's existing Claude Code config.

## Architecture

```
app.py          Textual App, layout, key bindings, bus event dispatch
agent.py        Agent class — subprocess lifecycle, session management, stream-json parsing
bus.py          asyncio.Queue event bus + live agent registry (MCP server reads from this)
mcp_server.py   Local SSE MCP server on localhost:18765 — team communication tools
main.py         Entry point — starts MCP server and Textual app concurrently
names.py        25 human name pool, colour assignment, random shuffle per session
logger.py       Crash logging (~/.claude-tui/logs/crash-*.log), chat export on demand
widgets/
  agent_list.py    Sidebar ListView with status icons and status text
  chat_view.py     Scrollable messenger-style bubble layout
  bubble.py        Rich Panel bubbles — left-aligned (agent), right-aligned (you)
  compose_bar.py   Input bar with message routing
  dm_screen.py     Per-agent modal DM screen (Escape to close, Ctrl+E to export)
  agent_detail.py  Raw stream-json log modal (Ctrl+D)
```

## How agents work

Each agent is an asyncio subprocess:
- First turn: `claude --print --output-format stream-json --verbose --mcp-config <path> --allowedTools <mcp-tools> --append-system-prompt <prompt> <message>`
- Follow-up turns: same flags plus `--resume <session_id>`
- Per-agent MCP config written to `/tmp/claude-tui-mcp-{Name}.json` with `?agent=Name` query param so the shared MCP server knows which agent is calling
- Each subprocess is spawned with `cwd=agent.cwd` so the correct `CLAUDE.md` is picked up

## MCP tools available to agents

| Tool | Description |
|------|-------------|
| `list_agents()` | Live roster — name, status, what they're working on |
| `message_agent(agent_name, message)` | DM a teammate; their reply appears in team chat |
| `set_working_directory(path)` | Change cwd for next turn; picks up that repo's CLAUDE.md |
| `send_message(message)` | Post to shared team chat |
| `ask_user(question)` | Block until human replies |
| `mark_done(summary)` | Signal task complete |
| `mark_blocked(reason)` | Signal blocked |
| `update_status(summary)` | Update sidebar status text |

## Message routing

When the user types in the compose bar:
1. Names at position 0 or after a separator (`-`, `—`, `,`, `;`) are treated as addressees
2. Names mid-sentence (e.g. "ask **Cleo** about X") are subjects, not recipients
3. If one agent resolved → send their segment; multiple → split at name boundaries, strip connector words
4. If no agent resolved → fall back to last active agent, then sidebar selection
5. If still nothing → global broadcast to all agents

## Task queue

Type `/task <description>` in the compose bar to add a task to the shared queue. Any idle agent is nudged immediately; busy agents call `claim_task()` automatically when they finish their current work.

| Tool | Behaviour |
|------|-----------|
| `claim_task()` | Pop the next task and return its description |
| `list_tasks()` | Preview the queue without claiming |

## Key bindings

| Key | Action |
|-----|--------|
| Ctrl+N | Spawn new agent |
| Ctrl+K | Kill selected agent |
| Ctrl+D | Raw stream-json detail for selected agent |
| Ctrl+E | Export chat log (or DM log when in DM screen) |
| Enter on agent in sidebar | Open DM screen |
| Escape | Close DM screen |

## Logs

- Crashes: `~/.claude-tui/logs/crash-<timestamp>.log`
- Chat exports: `~/.claude-tui/logs/chat-<timestamp>.txt`
- DM exports: same location, triggered by Ctrl+E in DM screen

## Adding new MCP tools

1. Add a `Tool(...)` entry to `_TOOLS` in `mcp_server.py`
2. Add a handler branch in `call_tool()` in the same file
3. Add the tool name (`mcp__team-chat__<name>`) to `_MCP_TOOLS` in `agent.py`
4. Mention it in `SYSTEM_PROMPT` in `agent.py` if agents should use it proactively
