# Claude Agent TUI

> **For Claude Code:** Keep this file up to date whenever behaviour, flags, tools, commands, or architecture change. Update it as part of the same change — do not wait to be asked.

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
mcp_server.py   Local SSE MCP server on localhost:18765 — team communication + permission hook endpoint
main.py         Entry point — starts MCP server and Textual app concurrently
names.py        25 human name pool, colour assignment, random shuffle per session
logger.py       Crash logging (~/.claude-tui/logs/crash-*.log), chat export on demand
widgets/
  agent_list.py    Sidebar ListView with status icons and status text
  chat_view.py     Scrollable messenger-style bubble layout
  bubble.py        Rich Panel bubbles + PermissionBubble for tool approval
  compose_bar.py   Input bar with @mention routing and autocomplete
  dm_screen.py     Per-agent modal DM screen (Escape to close, Ctrl+E to export)
  agent_detail.py  Raw stream-json log modal (Ctrl+D)
```

## How agents work

Each agent is an asyncio subprocess. Three files are written to `/tmp/` at spawn time:

| File | Purpose |
|------|---------|
| `/tmp/claude-tui-mcp-{Name}.json` | MCP config — merged from user's global settings, installed plugins, and the team-chat server |
| `/tmp/claude-tui-hook-{Name}.sh` | `PreToolUse` hook script — calls `/permission` endpoint before each tool use |
| `/tmp/claude-tui-settings-{Name}.json` | Claude settings file — registers the hook |

**First turn:**
```
claude --print --output-format stream-json --verbose
  --mcp-config /tmp/claude-tui-mcp-{Name}.json
  --strict-mcp-config
  --allowedTools mcp__team-chat__claim_task,...
  --settings /tmp/claude-tui-settings-{Name}.json
  --append-system-prompt <SYSTEM_PROMPT>
  <message>
```

**Follow-up turns:** same flags plus `--resume <session_id>`.

Each subprocess is spawned with `cwd=agent.cwd` so the correct `CLAUDE.md` is picked up. The MCP config includes:
1. `mcpServers` from `~/.claude/settings.json` and `settings.local.json`
2. `.mcp.json` from each installed Claude Code plugin
3. The `team-chat` SSE server (always last, can't be overridden)

## Permission system

A `PreToolUse` hook fires before every tool call. The hook script:
1. Checks the raw JSON for `"tool_name":"mcp__"` via `grep` — auto-allows all MCP tools immediately (no python3 dependency)
2. For everything else (Bash, Edit, Write, etc.), POSTs tool info to `http://localhost:18765/permission?agent={Name}` and blocks
3. The TUI shows a `PermissionBubble` with **Allow once / Allow session / Deny** buttons
4. The button click resolves an asyncio Future, the endpoint returns the decision, the hook exits 0 (allow) or 2 (deny)

"Allow session" remembers the decision for that agent+tool pair for the rest of the TUI session.

## MCP tools available to agents

| Tool | Description |
|------|-------------|
| `claim_task()` | Pop the next task from the shared queue |
| `list_tasks()` | Preview the queue without claiming |
| `list_agents()` | Live roster — name, status, what they're working on |
| `message_agent(agent_name, message)` | DM a teammate; their reply appears in team chat |
| `set_working_directory(path)` | Change cwd for next turn; picks up that repo's CLAUDE.md |
| `send_message(message)` | Post to shared team chat |
| `ask_user(question)` | Block until human replies |
| `mark_done(summary)` | Signal task complete |
| `mark_blocked(reason)` | Signal blocked |
| `update_status(summary)` | Update sidebar status text |

## Message routing

Type in the compose bar and press Enter:

| Input | Behaviour |
|-------|-----------|
| `@Name message` | Sends to that agent only (case-insensitive, strips `@Name` from message) |
| `@John do X @Mary do Y` | Splits at mention boundaries, each agent gets their segment |
| `@everyone message` | Broadcasts full text to all agents |
| `message` (no mention) | Routes to next available agent (idle → done → last active → first) |

Typing `@` shows an autocomplete bar above the input with matching agent names. Click a chip to complete.

## Task queue

Type `/task <description>` to add a task. Exactly one free agent is nudged; when they finish the STATUS handler chains to the next queued task.

## Compose bar commands

| Command | Action |
|---------|--------|
| `/task <description>` | Add a task to the shared queue |
| `/run <claude args>` | Suspend TUI and run an interactive `claude` command (e.g. `/run /login`, `/run /doctor`) |

`/run` works from both the main chat and DM screens.

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
