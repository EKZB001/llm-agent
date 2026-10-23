"""Tools for reading/writing daily nutritional logs and looking up food macros."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List
import urllib.parse
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"


def read_daily_log(date_str: str) -> Dict[str, Any]:
    """Read existing meals and cumulative daily totals for a specific date (DD-MM-YYYY).
    If the file does not exist, return an empty template with zeroed totals.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LOGS_DIR / f"{date_str}.json"

    if not filepath.exists():
        return {
            "date": date_str,
            "meals": [],
            "daily_totals": {
                "calories": 0.0,
                "protein": 0.0,
                "fats": 0.0,
                "carbs": 0.0,
                "sugars": 0.0,
                "salt": 0.0
            },
            "status": "new_day",
        }

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        data["status"] = "existing_log"
        return data


def lookup_local_food(query: str) -> List[Dict[str, Any]]:
    """Search for matching food items in the local nutrition database by name."""
    filepath = DATA_DIR / "nutrition_db.json"
    if not filepath.exists():
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        db: List[Dict[str, Any]] = json.load(f)

    clean_term = query.strip().lower()
    return [item for item in db if clean_term in item["name"].lower()]


def query_open_food_facts(product_name: str) -> Dict[str, Any]:
    """Query Open Food Facts API for up to 5 matching products with macros, sugars, salt, and serving size."""
    encoded_query = urllib.parse.quote(product_name.strip())
    url = (
        f"https://pl.openfoodfacts.org/cgi/search.pl?"
        f"search_terms={encoded_query}&search_simple=1&action=process&json=1&page_size=5"
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MacroTrackAgent/1.0 (student-project-learning)"},
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))

        products = data.get("products", [])
        if not products:
            return {
                "found": False,
                "message": f"Nie znaleziono produktu '{product_name}' w bazie Open Food Facts.",
            }

        candidates: List[Dict[str, Any]] = []
        for p in products[:5]:
            nutriments = p.get("nutriments", {})
            raw_name = p.get("product_name") or p.get("product_name_pl") or product_name
            brand = p.get("brands", "").strip()
            display_name = f"{brand} - {raw_name}".strip(" -") if brand else raw_name

            serving_weight = None
            if "serving_quantity" in p:
                try:
                    serving_weight = float(p["serving_quantity"])
                except (ValueError, TypeError):
                    serving_weight = None

            candidates.append({
                "name": display_name,
                "unit": "100g",
                "serving_weight_g": serving_weight,
                "calories": round(float(nutriments.get("energy-kcal_100g", 0.0)), 1),
                "protein": round(float(nutriments.get("proteins_100g", 0.0)), 1),
                "fats": round(float(nutriments.get("fat_100g", 0.0)), 1),
                "carbs": round(float(nutriments.get("carbohydrates_100g", 0.0)), 1),
                "sugars": round(float(nutriments.get("sugars_100g", 0.0)), 1),
                "salt": round(float(nutriments.get("salt_100g", 0.0)), 2),
                "source": "Open Food Facts API",
            })

        return {
            "found": True,
            "count": len(candidates),
            "candidates": candidates,
        }
    except Exception as exc:
        return {"found": False, "error": f"Błąd zapytania HTTP: {str(exc)}"}


def cache_food_item(item_data: Dict[str, Any]) -> Dict[str, Any]:
    """Append a newly retrieved or defined food item to local cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / "nutrition_db.json"

    db: List[Dict[str, Any]] = []
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            db = json.load(f)

    new_id = f"prod_{len(db) + 1:03d}"
    cached_record = {
        "id": new_id,
        "name": item_data["name"].lower(),
        "unit": item_data.get("unit", "100g"),
        "calories": round(float(item_data["calories"]), 1),
        "protein": round(float(item_data["protein"]), 1),
        "fats": round(float(item_data["fats"]), 1),
        "carbs": round(float(item_data["carbs"]), 1),
    }

    db.append(cached_record)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    return {"status": "success", "cached_item": cached_record}


def save_daily_log(date_str: str, updated_log: Dict[str, Any]) -> Dict[str, str]:
    """Persist the updated daily log with newly appended meals and accumulated totals."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LOGS_DIR / f"{date_str}.json"

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(updated_log, f, ensure_ascii=False, indent=2)

    return {"status": "success", "file": str(filepath)}


# Metadata schema definitions for model function calling
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_daily_log",
            "description": "Fetch the current daily meal entries and macronutrient totals for a given date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Date in DD-MM-YYYY format, e.g., '10-10-2026'.",
                    }
                },
                "required": ["date_str"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_local_food",
            "description": "Query the local JSON database for an ingredient and its nutritional values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Polish name of the product, e.g., 'kurczak', 'jajko', 'ryż'.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_open_food_facts",
            "description": "Search the public Open Food Facts database when an item is missing locally. Returns up to 5 matching candidates with brands and nutritional values per 100g.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Name of the missing product to query in the API.",
                    }
                },
                "required": ["product_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cache_food_item",
            "description": "Save an item discovered via Open Food Facts into the local database for offline reuse.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_data": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "unit": {"type": "string"},
                            "calories": {"type": "number"},
                            "protein": {"type": "number"},
                            "fats": {"type": "number"},
                            "carbs": {"type": "number"},
                        },
                        "required": ["name", "unit", "calories", "protein", "fats", "carbs"],
                    }
                },
                "required": ["item_data"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_daily_log",
            "description": "Persist the daily log with the new meal entry and updated cumulative totals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Date formatted as DD-MM-YYYY.",
                    },
                    "updated_log": {
                        "type": "object",
                        "description": "The complete daily log structure including updated 'meals' and 'daily_totals'.",
                    },
                },
                "required": ["date_str", "updated_log"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_REGISTRY = {
    "read_daily_log": read_daily_log,
    "lookup_local_food": lookup_local_food,
    "query_open_food_facts": query_open_food_facts,
    "cache_food_item": cache_food_item,
    "save_daily_log": save_daily_log,
}