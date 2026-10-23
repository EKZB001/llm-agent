"""Execution tracer for recording agent decisions, delays, and token costs."""

from __future__ import annotations
import json
from pathlib import Path
import time
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
TRACES_DIR = ROOT_DIR / "traces"
TRACE_FILE = TRACES_DIR / "trace.jsonl"

INPUT_COST_PER_M = 0.75
OUTPUT_COST_PER_M = 3.75
# dostosowane do cennika gemini 3.6 flash

CURRENT_SESSION_FILE: Path | None = None


def init_trace_file() -> None:
    """Ensure traces directory exists, clear previous run trace, and start a timestamped session file."""
    global CURRENT_SESSION_FILE
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    history_dir = TRACES_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    if TRACE_FILE.exists():
        TRACE_FILE.unlink()

    session_ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    CURRENT_SESSION_FILE = history_dir / f"trace_{session_ts}.jsonl"


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate approximate USD cost for token usage."""
    in_cost = (prompt_tokens / 1_000_000) * INPUT_COST_PER_M
    out_cost = (completion_tokens / 1_000_000) * OUTPUT_COST_PER_M
    return round(in_cost + out_cost, 6)


def record_step(entry: Dict[str, Any]) -> None:
    """Append a single decision step into trace.jsonl and the timestamped history trace."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    with open(TRACE_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    if CURRENT_SESSION_FILE:
        with open(CURRENT_SESSION_FILE, "a", encoding="utf-8") as f:
            f.write(line)