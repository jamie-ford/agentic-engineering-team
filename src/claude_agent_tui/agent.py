"""Agent: wraps a claude CLI subprocess session."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from enum import Enum
from pathlib import Path

from . import bus as bus_mod
from . import logger
from .bus import BusEvent, EventType
from .mcp_server import PORT

SYSTEM_PROMPT = """\
You are {name}, a software engineer on a small, fast-moving team. You talk the way engineers actually talk — casual, direct, no corporate speak.

You have team-chat tools:
  - claim_task(): pop the next task from the shared queue and work on it
  - list_tasks(): preview what's queued without claiming
  - list_agents(): see who else is on the team and what they're working on
  - message_agent(agent_name, message): send a direct message to a teammate — their reply appears in team chat
  - set_working_directory(path): set your working directory to a repo (e.g. /code/repo1) — picks up that repo's CLAUDE.md from your next turn
  - update_status(summary): short phrase for the sidebar, e.g. "fixing login bug" — call this whenever you switch tasks
  - send_message(message): posts to the shared team channel — use for updates that the whole team should see
  - ask_user(question): blocks until the human replies — use when genuinely stuck or need a decision
  - mark_done(summary): signals you're done; always call this when finishing a task
  - mark_blocked(reason): signals you're blocked; always call this when you can't continue

How to behave:
- Be natural and concise. Say "done, tests all pass — anything else?" not "I have successfully completed the task."
- When you start a real task: call update_status, then ONE send_message briefly describing your plan.
- When you finish any task — whether from main chat or a DM — always call mark_done with a short summary, e.g. "Added auth tests to repo1". This is the only thing shown in the main team channel, so always do it regardless of how the task arrived.
- When assigned to a specific repo, call set_working_directory() first so you pick up that project's rules and context.
- Work with teammates when it makes sense — use list_agents() to see who's around, message_agent() to delegate or get context.

Looking for work (do this silently — no send_message during this process):
1. Call claim_task(). If you get a task, post ONE message: "picking up: <task>" then do the work.
2. If queue is empty, look around for TODOs, broken tests, etc. Do this silently.
3. Only if there is genuinely nothing to do, call mark_blocked with "waiting for work". Say it once, then stop.

Never narrate your queue checks, file exploration, or "nothing found" conclusions to the team chat. Only speak up when you have real news: task claimed, work done, or truly blocked.\
"""


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    DONE = "done"


class Agent:
    def __init__(self, name: str):
        self.name = name
        self.status = AgentStatus.IDLE
        self.status_text: str = ""
        self.session_id: str | None = None
        self.stream_log: list[dict] = []
        self.dm_history: list[dict] = []
        self.cwd: str = os.getcwd()
        self._mcp_config_path: Path = self._write_mcp_config()
        self._hook_script_path: Path = self._write_permission_hook()
        self._settings_path: Path = self._write_hook_settings()
        self._running_task: asyncio.Task | None = None
        self._current_is_dm: bool = False

    def _write_mcp_config(self) -> Path:
        servers: dict = {}

        # 1. Global user settings files
        for p in [
            Path.home() / ".claude" / "settings.json",
            Path.home() / ".claude" / "settings.local.json",
        ]:
            try:
                servers.update(json.loads(p.read_text()).get("mcpServers", {}))
            except Exception:
                pass

        # 2. Installed Claude Code plugins (each has its own .mcp.json)
        plugins_index = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        try:
            for versions in json.loads(plugins_index.read_text()).get("plugins", {}).values():
                for install in versions:
                    mcp_file = Path(install["installPath"]) / ".mcp.json"
                    try:
                        servers.update(json.loads(mcp_file.read_text()).get("mcpServers", {}))
                    except Exception:
                        pass
        except Exception:
            pass

        # Always include team-chat last so it can never be overridden
        servers["team-chat"] = {
            "type": "sse",
            "url": f"http://localhost:{PORT}/sse?agent={self.name}",
        }

        path = Path(f"/tmp/claude-tui-mcp-{self.name}.json")
        path.write_text(json.dumps({"mcpServers": servers}, indent=2))
        return path

    def _claude_path(self) -> str:
        path = shutil.which("claude")
        if not path:
            raise RuntimeError("claude CLI not found in PATH")
        return path

    _MCP_TOOLS = [
        "mcp__team-chat__claim_task",
        "mcp__team-chat__list_tasks",
        "mcp__team-chat__list_agents",
        "mcp__team-chat__message_agent",
        "mcp__team-chat__set_working_directory",
        "mcp__team-chat__send_message",
        "mcp__team-chat__ask_user",
        "mcp__team-chat__mark_done",
        "mcp__team-chat__mark_blocked",
        "mcp__team-chat__update_status",
    ]

    def _write_permission_hook(self) -> Path:
        script = f"""\
#!/bin/bash
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")

# Auto-allow MCP tools (they have their own access control)
if [[ "$TOOL_NAME" == mcp__* ]]; then
    exit 0
fi

# Ask TUI for permission — blocks until user responds (up to 300s)
RESPONSE=$(echo "$INPUT" | curl -sf --max-time 310 \\
    -X POST "http://localhost:{PORT}/permission?agent={self.name}" \\
    -H "Content-Type: application/json" \\
    --data-binary @-)

if [ $? -ne 0 ] || [ -z "$RESPONSE" ]; then
    exit 0  # TUI unavailable — allow by default
fi

DECISION=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('decision','allow'))" 2>/dev/null || echo "allow")

[ "$DECISION" = "deny" ] && exit 2 || exit 0
"""
        path = Path(f"/tmp/claude-tui-hook-{self.name}.sh")
        path.write_text(script)
        path.chmod(0o755)
        return path

    def _write_hook_settings(self) -> Path:
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(self._hook_script_path),
                            }
                        ]
                    }
                ]
            }
        }
        path = Path(f"/tmp/claude-tui-settings-{self.name}.json")
        path.write_text(json.dumps(settings, indent=2))
        return path

    def _build_cmd(self, message: str, is_first_turn: bool) -> list[str]:
        cmd = [
            self._claude_path(),
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--mcp-config", str(self._mcp_config_path),
            "--strict-mcp-config",
            "--allowedTools", ",".join(self._MCP_TOOLS),
            "--settings", str(self._settings_path),
        ]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        elif is_first_turn:
            cmd += ["--append-system-prompt", SYSTEM_PROMPT.format(name=self.name)]
        cmd.append(message)
        return cmd

    async def send(self, message: str, from_dm: bool = False) -> None:
        self._current_is_dm = from_dm
        if from_dm:
            self.dm_history.append({"role": "user", "text": message})

        if self._running_task and not self._running_task.done():
            if bus_mod.resolve_ask(self.name, message):
                return
            prev = self._running_task
            async def _followup():
                try:
                    await prev
                except Exception:
                    pass
                await self._run_turn(message, is_first_turn=False)
            self._running_task = asyncio.create_task(_followup())
            return

        self._running_task = asyncio.create_task(
            self._run_turn(message, is_first_turn=self.session_id is None)
        )

    async def _run_turn(self, message: str, is_first_turn: bool = False) -> None:
        self.status = AgentStatus.WORKING
        await bus_mod.post(BusEvent(
            type=EventType.STATUS,
            agent_name=self.name,
            payload={"status": "working"},
        ))

        cmd = self._build_cmd(message, is_first_turn)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TERM": "dumb"},
                cwd=self.cwd,
            )

            assert proc.stdout is not None
            assert proc.stderr is not None

            async def _drain_stderr():
                async for raw in proc.stderr:
                    line = raw.decode(errors="replace").rstrip()
                    if line:
                        logger.crash(RuntimeError(f"[stderr:{self.name}] {line}"))
                        self.stream_log.append({"type": "stderr", "text": line})

            stderr_task = asyncio.create_task(_drain_stderr())

            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.crash(ValueError(f"Non-JSON stdout from claude: {line!r}"))
                    continue
                self.stream_log.append(event)
                await self._handle_stream_event(event)

            await proc.wait()
            await stderr_task

        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                proc.kill()
            raise
        except Exception as exc:
            logger.crash(exc)
            await bus_mod.post(BusEvent(
                type=EventType.MESSAGE,
                agent_name=self.name,
                payload={"message": f"[Error] {exc}"},
            ))

        if self.status == AgentStatus.WORKING:
            # Agent finished without calling mark_done/mark_blocked — surface what they did
            summary = self.status_text or "task complete"
            self.status = AgentStatus.DONE
            await bus_mod.post(BusEvent(
                type=EventType.STATUS,
                agent_name=self.name,
                payload={"status": "done", "message": summary},
            ))

    async def _handle_stream_event(self, event: dict) -> None:
        etype = event.get("type", "")

        if "session_id" in event and not self.session_id:
            self.session_id = event["session_id"]

        if etype == "assistant":
            content = event.get("message", {}).get("content", [])
            text_parts = [
                block["text"]
                for block in content
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
            ]
            if text_parts:
                full_text = "\n".join(text_parts)

                if "Not logged in" in full_text:
                    await bus_mod.post(BusEvent(
                        type=EventType.STATUS,
                        agent_name=self.name,
                        payload={
                            "status": "blocked",
                            "message": "Not logged in — type `/run /login` in the TUI to authenticate",
                        },
                    ))
                    return

                self.dm_history.append({"role": "agent", "text": full_text})
                await bus_mod.post(BusEvent(
                    type=EventType.DM_REPLY,
                    agent_name=self.name,
                    payload={"text": full_text, "from_dm": self._current_is_dm},
                ))

    def kill(self) -> None:
        if self._running_task and not self._running_task.done():
            self._running_task.cancel()
        self.status = AgentStatus.IDLE
        for path in (self._mcp_config_path, self._hook_script_path, self._settings_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
