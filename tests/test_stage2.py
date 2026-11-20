"""Stage 2 tests: typed tools, security policy gates, MCP servers, and multi-step decisions."""

from __future__ import annotations
import json
from pathlib import Path
import anyio
import pytest

from agent_project.policy import (
    ToolCategory,
    execute_tool_with_policy,
    get_tool_category,
    is_tool_allowed,
)
from agent_project.tools import (
    clear_daily_log,
    delete_meal_entry,
    log_meal_entry,
    read_daily_log,
)
from agent_project.mcp_server import mcp as nutrition_mcp
from agent_project.fitness_mcp_server import mcp as fitness_mcp
from agent_project.mcp_client import MCPClientHub
from mcp import Client


# ============================================================================
# 1. TESTS FOR THREE KINDS OF TOOLS & POLICY VALIDATION
# ============================================================================

def test_tool_categorization() -> None:
    """Verify each tool is categorized into the expected architectural risk level."""
    assert get_tool_category("lookup_local_food") == ToolCategory.READ_ONLY
    assert get_tool_category("read_daily_log") == ToolCategory.READ_ONLY
    assert get_tool_category("log_meal_entry") == ToolCategory.REVERSIBLE_WRITE
    assert get_tool_category("delete_meal_entry") == ToolCategory.SENSITIVE
    assert get_tool_category("clear_daily_log") == ToolCategory.SENSITIVE


def test_policy_blocks_unauthorized_tool() -> None:
    """Policy must intercept and reject tools that do not exist in the authorized map."""
    res = execute_tool_with_policy("drop_all_tables", "{}")
    assert res["status"] == "error"
    assert res["error_type"] == "UNAUTHORIZED_TOOL"


def test_policy_validates_pydantic_arguments() -> None:
    """Arguments violating Pydantic schema (e.g. invalid date or empty query) must be rejected."""
    # Invalid date regex (should be DD-MM-YYYY)
    bad_date_res = execute_tool_with_policy("read_daily_log", '{"date_str": "2026/10/24"}')
    assert bad_date_res["status"] == "error"
    assert bad_date_res["error_type"] == "VALIDATION_FAILED"

    # Empty query string
    empty_query_res = execute_tool_with_policy("lookup_local_food", '{"query": ""}')
    assert empty_query_res["status"] == "error"
    assert empty_query_res["error_type"] == "VALIDATION_FAILED"


# ============================================================================
# 2. TESTS FOR REVERSIBLE WRITE AND SENSITIVE CONFIRMATION GATE
# ============================================================================

def test_reversible_write_and_sensitive_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify log_meal_entry creates a unique meal_id, and delete_meal_entry reverses it.
    Also verify confirmation denial blocks deletion, while approval permits it.
    """
    monkeypatch.setattr("agent_project.tools.LOGS_DIR", tmp_path)
    test_date = "15-11-2026"

    # Step 1: Log a meal (reversible write)
    write_args = json.dumps({
        "date_str": test_date,
        "meal_name": "Kolacja: Twaróg z rzodkiewką",
        "items": [
            {
                "name": "twaróg chudy",
                "amount_g": 200,
                "calories": 170.0,
                "protein": 36.0,
                "fats": 1.0,
                "carbs": 7.0,
            }
        ],
    })

    log_res = execute_tool_with_policy("log_meal_entry", write_args)
    assert log_res["status"] == "success"
    meal_id = log_res["result"]["meal_id"]
    assert meal_id.startswith("meal_")

    # Verify log state on disk
    day_state = read_daily_log(test_date)
    assert len(day_state["meals"]) == 1
    assert day_state["daily_totals"]["calories"] == 170.0

    # Step 2: Attempt deletion with human confirmation DENIED
    del_args = json.dumps({"date_str": test_date, "meal_id": meal_id})
    deny_res = execute_tool_with_policy("delete_meal_entry", del_args, confirm_handler=lambda _tool, _args: False)

    assert deny_res["status"] == "confirmation_denied"
    # State MUST be unchanged!
    day_state_after_deny = read_daily_log(test_date)
    assert len(day_state_after_deny["meals"]) == 1

    # Step 3: Attempt deletion with human confirmation APPROVED
    approve_res = execute_tool_with_policy("delete_meal_entry", del_args, confirm_handler=lambda _tool, _args: True)

    assert approve_res["status"] == "success"
    assert approve_res["result"]["remaining_meals_count"] == 0
    # State is now reversed
    day_state_after_approve = read_daily_log(test_date)
    assert len(day_state_after_approve["meals"]) == 0
    assert day_state_after_approve["daily_totals"]["calories"] == 0.0


# ============================================================================
# 3. TESTS FOR MCP SERVERS (NUTRITION & FITNESS)
# ============================================================================

def test_nutrition_mcp_server() -> None:
    """Verify Nutrition MCP Server exposes tools and returns expected schema and results."""
    async def run():
        async with Client(nutrition_mcp) as client:
            tools_list = await client.list_tools()
            tool_names = [t.name for t in tools_list.tools]
            assert "lookup_food" in tool_names
            assert "read_log" in tool_names
            assert "log_meal" in tool_names
            assert "delete_meal" in tool_names

            # Call lookup_food through MCP
            res = await client.call_tool("lookup_food", {"query": "kurczak"})
            assert len(res.content) >= 1
            item0 = json.loads(res.content[0].text)
            assert isinstance(item0, dict)
            assert "kurczak" in item0["name"].lower()
            assert "calories" in item0

    anyio.run(run)


def test_fitness_mcp_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Fitness MCP Server calculates MET calories and handles fallback intensities."""
    monkeypatch.setattr("agent_project.fitness_mcp_server.WORKOUTS_FILE", tmp_path / "workouts.json")

    async def run():
        async with Client(fitness_mcp) as client:
            # 1. Exact sport match (bieganie, MET 10.0, 75kg * 10 * 30/60 = 375 kcal)
            res1 = await client.call_tool("estimate_burned_calories", {"activity": "bieganie", "duration_minutes": 30})
            data1 = json.loads(res1.content[0].text)
            assert data1["burned_calories"] == 375.0
            assert data1["match_type"] == "exact_sport_match"

            # 2. Unknown sport fallback (e.g. padel with high intensity, MET 10.0 -> 375 kcal, moderate MET 6.0 -> 225 kcal)
            res2 = await client.call_tool("estimate_burned_calories", {"activity": "nieznana_gra_xyz", "duration_minutes": 30, "intensity": "moderate"})
            data2 = json.loads(res2.content[0].text)
            assert data2["burned_calories"] == 225.0
            assert data2["match_type"] == "intensity_fallback"

            # 3. Log workout through MCP
            res3 = await client.call_tool("log_workout", {"date_str": "20-11-2026", "activity": "basen", "duration_minutes": 45})
            data3 = json.loads(res3.content[0].text)
            assert data3["status"] == "success"
            assert data3["workout_entry"]["activity"] == "basen"

    anyio.run(run)


def test_mcp_client_hub_orchestration() -> None:
    """Verify MCPClientHub registers multiple servers, aggregates tools, and executes calls."""
    async def run():
        hub = MCPClientHub()
        hub.register_server("nutrition", nutrition_mcp)
        hub.register_server("fitness", fitness_mcp)

        tools = await hub.initialize_tools()
        names = [t["function"]["name"] for t in tools]

        # Verify tools from both servers were unified
        assert "lookup_food" in names
        assert "estimate_burned_calories" in names

        # Call fitness tool via hub
        res = await hub.call_tool("estimate_burned_calories", '{"activity": "rower", "duration_minutes": 60}')
        assert res["status"] == "success"
        assert res["result"]["burned_calories"] > 0

    anyio.run(run)
