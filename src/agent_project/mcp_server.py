"""MacroTrack Nutrition MCP Server.

Exposes domain tools over the Model Context Protocol (stdio transport)
reusing the exact implementations from agent_project.tools.
"""

from __future__ import annotations
from typing import Any, Dict, List
from mcp.server import MCPServer

from agent_project.tools import (
    delete_meal_entry as _delete_meal_entry,
    log_meal_entry as _log_meal_entry,
    lookup_local_food as _lookup_local_food,
    read_daily_log as _read_daily_log,
)

# Initialize MCP Server instance
mcp = MCPServer("MacroTrack-Nutrition")


@mcp.tool()
def lookup_food(query: str) -> List[Dict[str, Any]]:
    """Search for matching food items in the local nutrition database by name.
    Returns list of items with calories, protein, fats, carbs, sugars, and salt per 100g.
    """
    return _lookup_local_food(query=query)


@mcp.tool()
def read_log(date_str: str) -> Dict[str, Any]:
    """Fetch existing meal entries and cumulative daily macronutrient totals for a date (DD-MM-YYYY)."""
    return _read_daily_log(date_str=date_str)


@mcp.tool()
def log_meal(
    date_str: str,
    meal_name: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Append a new structured meal entry with items and nutritional values into the daily log."""
    return _log_meal_entry(date_str=date_str, meal_name=meal_name, items=items)


@mcp.tool()
def delete_meal(date_str: str, meal_id: str) -> Dict[str, Any]:
    """Permanently delete a meal entry by meal_id from the daily log."""
    return _delete_meal_entry(date_str=date_str, meal_id=meal_id)


if __name__ == "__main__":
    # Serve over standard I/O (default transport)
    mcp.run()
