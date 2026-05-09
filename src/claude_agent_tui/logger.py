"""Crash logger and on-demand chat exporter."""

from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

_log_dir = Path.home() / ".claude-tui" / "logs"
_crash_file: Path | None = None


def init() -> Path:
    """Create the log directory and return the crash log path for this session."""
    global _crash_file
    _log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    _crash_file = _log_dir / f"crash-{ts}.log"
    return _crash_file


def crash(exc: BaseException) -> None:
    if _crash_file is None:
        return
    ts = datetime.now().strftime("%H:%M:%S")
    with _crash_file.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] === CRASH: {type(exc).__name__}: {exc} ===\n")
        f.write(traceback.format_exc())
        f.write("=" * 60 + "\n\n")


def export_chat(lines: list[str]) -> Path:
    """Write plain-text chat lines to a timestamped export file. Returns path."""
    _log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = _log_dir / f"chat-{ts}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def crash_path() -> Path | None:
    return _crash_file
