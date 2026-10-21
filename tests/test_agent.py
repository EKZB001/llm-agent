"""Unit tests for tools, tracing, and agent loop execution."""

from pathlib import Path
import json
import pytest

from agent_project.agent import execute_tool_call, run_agent_loop
from agent_project.tools import lookup_local_food, read_daily_log
from agent_project.tracing import calculate_cost, init_trace_file, record_step


def test_read_daily_log_new_day() -> None:
    data = read_daily_log("99-99-9999")
    assert data["status"] == "new_day"
    assert data["date"] == "99-99-9999"
    assert data["daily_totals"]["calories"] == 0.0
    assert data["meals"] == []


def test_read_daily_log_existing() -> None:
    data = read_daily_log("10-10-2026")
    assert data["status"] == "existing_log"
    assert "daily_totals" in data
    assert len(data["meals"]) >= 1


def test_lookup_local_food_found() -> None:
    items = lookup_local_food("kurczak")
    assert len(items) >= 1
    assert any("kurczak" in item["name"].lower() for item in items)


def test_lookup_local_food_not_found() -> None:
    items = lookup_local_food("produkt_ktory_na_pewno_nie_istnieje_xyz")
    assert items == []


def test_execute_tool_call_valid() -> None:
    res = execute_tool_call("read_daily_log", '{"date_str": "99-99-9999"}')
    assert "result" in res
    assert res["result"]["status"] == "new_day"


def test_execute_tool_call_invalid_name() -> None:
    res = execute_tool_call("non_existent_tool", "{}")
    assert "error" in res


def test_execute_tool_call_malformed_json() -> None:
    res = execute_tool_call("read_daily_log", "{bad_json")
    assert "error" in res


def test_calculate_cost() -> None:
    cost = calculate_cost(1000, 500)
    assert cost > 0.0


def test_record_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace_file = tmp_path / "test_trace.jsonl"
    monkeypatch.setattr("agent_project.tracing.TRACES_DIR", tmp_path)
    monkeypatch.setattr("agent_project.tracing.TRACE_FILE", trace_file)

    init_trace_file()
    record_step({"step": 1, "status": "IN_PROGRESS"})
    record_step({"step": 2, "status": "COMPLETED"})

    lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1["step"] == 1
    assert rec2["status"] == "COMPLETED"
