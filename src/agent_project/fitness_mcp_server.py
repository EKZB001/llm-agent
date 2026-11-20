"""MacroTrack Fitness & Workouts MCP Server.

Provides tools for calculating calorie burn using sports science MET standards
and tracking daily physical activities. Runs over stdio transport.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List
from mcp.server import MCPServer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXERCISE_DB_FILE = PROJECT_ROOT / "data" / "exercise_db.json"
WORKOUTS_FILE = PROJECT_ROOT / "data" / "workouts.json"

mcp = MCPServer("MacroTrack-Fitness")

# Standard MET equivalents for default intensities
DEFAULT_INTENSITY_MET = {
    "low": 3.5,       # ~4.4 kcal/min for 75kg
    "moderate": 6.0,  # ~7.5 kcal/min for 75kg
    "high": 10.0,     # ~12.5 kcal/min for 75kg
}


def _load_exercises() -> List[Dict[str, Any]]:
    if not EXERCISE_DB_FILE.exists():
        return []
    with open(EXERCISE_DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_workouts() -> List[Dict[str, Any]]:
    if not WORKOUTS_FILE.exists():
        return []
    with open(WORKOUTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_workouts(workouts: List[Dict[str, Any]]) -> None:
    WORKOUTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WORKOUTS_FILE, "w", encoding="utf-8") as f:
        json.dump(workouts, f, ensure_ascii=False, indent=2)


@mcp.tool()
def estimate_burned_calories(
    activity: str,
    duration_minutes: int,
    intensity: str = "moderate",
    user_weight_kg: float = 75.0,
) -> Dict[str, Any]:
    """Estimate calories burned for a physical activity using the MET formula.
    Supports ~25 specific sports, with fallback to intensity categories (low, moderate, high).
    Formula: Calories = MET * weight_kg * (duration_minutes / 60)
    """
    clean_name = activity.strip().lower()
    exercises = _load_exercises()

    # Search for matching sport in database
    matched_ex = next((ex for ex in exercises if ex["name"].lower() in clean_name or clean_name in ex["name"].lower()), None)

    if matched_ex:
        met = matched_ex["met"]
        match_type = "exact_sport_match"
        note = f"Dopasowano do bazy dyscyplin: '{matched_ex['name']}' (MET {met})."
    else:
        # Fallback to requested intensity
        intensity_clean = intensity.strip().lower()
        met = DEFAULT_INTENSITY_MET.get(intensity_clean, DEFAULT_INTENSITY_MET["moderate"])
        match_type = "intensity_fallback"
        note = (
            f"Dyscyplina '{activity}' nie występuje bezpośrednio w bazie. "
            f"Zastosowano bezpieczny przelicznik dla poziomu intensywności '{intensity_clean}' (MET {met})."
        )

    burned = round(met * user_weight_kg * (duration_minutes / 60.0), 1)

    return {
        "activity": activity,
        "duration_minutes": duration_minutes,
        "burned_calories": burned,
        "met_used": met,
        "match_type": match_type,
        "note": note,
    }


@mcp.tool()
def log_workout(
    date_str: str,
    activity: str,
    duration_minutes: int,
    intensity: str = "moderate",
    user_weight_kg: float = 75.0,
) -> Dict[str, Any]:
    """Record a completed workout session with computed calorie burn into data/workouts.json."""
    burn_info = estimate_burned_calories(
        activity=activity,
        duration_minutes=duration_minutes,
        intensity=intensity,
        user_weight_kg=user_weight_kg,
    )

    workouts = _load_workouts()
    workout_id = f"work_{len(workouts) + 1:03d}"

    entry = {
        "workout_id": workout_id,
        "date": date_str,
        "activity": activity,
        "duration_minutes": duration_minutes,
        "burned_calories": burn_info["burned_calories"],
        "match_type": burn_info["match_type"],
    }
    workouts.append(entry)
    _save_workouts(workouts)

    # Compute daily total burned
    daily_total = round(sum(w["burned_calories"] for w in workouts if w.get("date") == date_str), 1)

    return {
        "status": "success",
        "workout_entry": entry,
        "daily_total_burned_calories": daily_total,
    }


@mcp.tool()
def get_daily_workouts(date_str: str) -> Dict[str, Any]:
    """Retrieve all logged workouts and cumulative burned calories for a date (DD-MM-YYYY)."""
    workouts = _load_workouts()
    days_workouts = [w for w in workouts if w.get("date") == date_str]
    total_burned = round(sum(w.get("burned_calories", 0.0) for w in days_workouts), 1)

    return {
        "date": date_str,
        "workouts_count": len(days_workouts),
        "total_burned_calories": total_burned,
        "workouts": days_workouts,
    }


if __name__ == "__main__":
    mcp.run()
