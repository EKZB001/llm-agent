"""Execution tracer for recording agent decisions, delays, and token costs."""

from __future__ import annotations
import json
from pathlib import Path
import time
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
TRACES_DIR = ROOT_DIR / "traces"
TRACE_FILE = TRACES_DIR / "trace.jsonl"

INPUT_COST_PER_M = 0.15
OUTPUT_COST_PER_M = 0.60


def init_trace_file() -> None:
    """Ensure traces directory exists and clear previous run trace."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    if TRACE_FILE.exists():
        TRACE_FILE.unlink()


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate approximate USD cost for token usage."""
    in_cost = (prompt_tokens / 1_000_000) * INPUT_COST_PER_M
    out_cost = (completion_tokens / 1_000_000) * OUTPUT_COST_PER_M
    return round(in_cost + out_cost, 6)


def record_step(entry: Dict[str, Any]) -> None:
    """Append a single decision step into trace.jsonl."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRACE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")