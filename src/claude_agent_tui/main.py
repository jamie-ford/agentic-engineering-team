"""Entry point: starts MCP server as background task then runs the TUI."""

from __future__ import annotations

import asyncio
import sys
import traceback

from . import logger
from .mcp_server import start_server
from .app import AgentTuiApp


async def _run() -> None:
    mcp_task = asyncio.create_task(start_server())
    await asyncio.sleep(0.5)

    app = AgentTuiApp()
    try:
        await app.run_async()
    finally:
        mcp_task.cancel()
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    crash_path = logger.init()

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.crash(exc)
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"CRASH: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"Full traceback written to:\n  {crash_path}", file=sys.stderr)
        print('='*60, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
    finally:
        cp = logger.crash_path()
        if cp and cp.exists() and cp.stat().st_size > 0:
            print(f"\nCrash log: {cp}")


if __name__ == "__main__":
    main()
