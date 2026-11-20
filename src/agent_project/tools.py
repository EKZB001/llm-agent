"""Tools for reading/writing nutritional logs and looking up food macros.

Categorized into three distinct architectural kinds:
- Read-only: lookup_local_food, read_daily_log, query_open_food_facts
- Reversible write: log_meal_entry, cache_food_item, save_daily_log
- Sensitive: delete_meal_entry, clear_daily_log
"""

from __future__ import annotations
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"


# ============================================================================
# 1. READ-ONLY TOOLS (Odczyt danych, brak efektów ubocznych)
# ============================================================================

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
                "salt": 0.0,
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
        f"https://world.openfoodfacts.org/api/v2/search?"
        f"search_terms={encoded_query}&page_size=5"
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MacroTrackApp - Python - Version 2.0 - contact: student@example.com"},
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


# ============================================================================
# 2. REVERSIBLE WRITE TOOLS (Odwracalny zapis ze stanem sukcesu / błędu)
# ============================================================================

def cache_food_item(item_data: Dict[str, Any]) -> Dict[str, Any]:
    """Append a newly retrieved or defined food item to local cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / "nutrition_db.json"

    db: List[Dict[str, Any]] = []
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            db = json.load(f)

    clean_name = item_data["name"].strip().lower()
    for existing in db:
        if existing.get("name", "").strip().lower() == clean_name:
            return {"status": "already_cached", "cached_item": existing}

    new_id = f"prod_{len(db) + 1:03d}"
    cached_record = {
        "id": new_id,
        "name": clean_name,
        "unit": item_data.get("unit", "100g"),
        "calories": round(float(item_data["calories"]), 1),
        "protein": round(float(item_data["protein"]), 1),
        "fats": round(float(item_data["fats"]), 1),
        "carbs": round(float(item_data["carbs"]), 1),
        "sugars": round(float(item_data.get("sugars", 0.0)), 1),
        "salt": round(float(item_data.get("salt", 0.0)), 2),
    }

    db.append(cached_record)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    return {"status": "success", "cached_item": cached_record}


def log_meal_entry(
    date_str: str,
    meal_name: str,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Append a new structured meal entry to the daily log and update cumulative totals.
    Generates a unique meal_id that makes this write operation reversible via delete_meal_entry.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LOGS_DIR / f"{date_str}.json"

    current_data = read_daily_log(date_str)
    meals = current_data.get("meals", [])

    meal_calories = 0.0
    meal_protein = 0.0
    meal_fats = 0.0
    meal_carbs = 0.0
    meal_sugars = 0.0
    meal_salt = 0.0

    sanitized_items = []
    for it in items:
        amt = float(it.get("amount_g", 100))
        cal = float(it.get("calories", 0.0))
        prot = float(it.get("protein", 0.0))
        fat = float(it.get("fats", 0.0))
        carb = float(it.get("carbs", 0.0))
        sug = float(it.get("sugars", 0.0))
        salt = float(it.get("salt", 0.0))

        meal_calories += cal
        meal_protein += prot
        meal_fats += fat
        meal_carbs += carb
        meal_sugars += sug
        meal_salt += salt

        sanitized_items.append({
            "name": it.get("name", "Produkt"),
            "amount_g": amt,
            "calories": round(cal, 1),
            "protein": round(prot, 2),
            "fats": round(fat, 2),
            "carbs": round(carb, 2),
            "sugars": round(sug, 2),
            "salt": round(salt, 3),
        })

    meal_id = f"meal_{int(time.time())}_{len(meals) + 1:02d}"
    new_meal = {
        "meal_id": meal_id,
        "name": meal_name,
        "calories": round(meal_calories, 1),
        "protein": round(meal_protein, 2),
        "fats": round(meal_fats, 2),
        "carbs": round(meal_carbs, 2),
        "sugars": round(meal_sugars, 2),
        "salt": round(meal_salt, 3),
        "items": sanitized_items,
    }

    meals.append(new_meal)

    # Recalculate daily totals
    totals = {
        "calories": round(sum(m.get("calories", 0.0) for m in meals), 1),
        "protein": round(sum(m.get("protein", 0.0) for m in meals), 2),
        "fats": round(sum(m.get("fats", 0.0) for m in meals), 2),
        "carbs": round(sum(m.get("carbs", 0.0) for m in meals), 2),
        "sugars": round(sum(m.get("sugars", 0.0) for m in meals), 2),
        "salt": round(sum(m.get("salt", 0.0) for m in meals), 3),
    }

    updated_log = {
        "date": date_str,
        "daily_totals": totals,
        "meals": meals,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(updated_log, f, ensure_ascii=False, indent=2)

    return {
        "status": "success",
        "meal_id": meal_id,
        "logged_meal": new_meal,
        "daily_totals": totals,
        "file": str(filepath),
    }


def save_daily_log(date_str: str, updated_log: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the daily log with raw meal entry and updated totals (legacy compatibility)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LOGS_DIR / f"{date_str}.json"

    # Ensure all meals have meal_id for reversible operations
    meals = updated_log.get("meals", [])
    for idx, meal in enumerate(meals):
        if "meal_id" not in meal:
            meal["meal_id"] = f"meal_legacy_{idx + 1:02d}"

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(updated_log, f, ensure_ascii=False, indent=2)

    return {"status": "success", "file": str(filepath)}


# ============================================================================
# 3. SENSITIVE TOOLS (Operacje wrażliwe/destrukcyjne - wymagają zgody usera)
# ============================================================================

def delete_meal_entry(date_str: str, meal_id: str) -> Dict[str, Any]:
    """Permanently delete a single meal from the daily log by meal_id.
    Requires explicit human confirmation before invocation.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LOGS_DIR / f"{date_str}.json"

    if not filepath.exists():
        return {
            "status": "error",
            "message": f"Dziennik dla dnia {date_str} nie istnieje.",
        }

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    meals = data.get("meals", [])
    matching_meal = next((m for m in meals if m.get("meal_id") == meal_id), None)
    if not matching_meal:
        # Also allow matching by meal name case-insensitively
        matching_meal = next((m for m in meals if meal_id.lower() in m.get("name", "").lower()), None)

    if not matching_meal:
        return {
            "status": "not_found",
            "message": f"Nie znaleziono posiłku o identyfikatorze '{meal_id}' w dniu {date_str}.",
        }

    meals.remove(matching_meal)

    totals = {
        "calories": round(sum(m.get("calories", 0.0) for m in meals), 1),
        "protein": round(sum(m.get("protein", 0.0) for m in meals), 2),
        "fats": round(sum(m.get("fats", 0.0) for m in meals), 2),
        "carbs": round(sum(m.get("carbs", 0.0) for m in meals), 2),
        "sugars": round(sum(m.get("sugars", 0.0) for m in meals), 2),
        "salt": round(sum(m.get("salt", 0.0) for m in meals), 3),
    }

    data["meals"] = meals
    data["daily_totals"] = totals

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "status": "success",
        "deleted_meal": matching_meal,
        "remaining_meals_count": len(meals),
        "daily_totals": totals,
    }


def clear_daily_log(date_str: str) -> Dict[str, Any]:
    """Wipe out all meals and reset cumulative totals to 0 for a given date.
    High-impact sensitive operation requiring explicit confirmation.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LOGS_DIR / f"{date_str}.json"

    empty_log = {
        "date": date_str,
        "meals": [],
        "daily_totals": {
            "calories": 0.0,
            "protein": 0.0,
            "fats": 0.0,
            "carbs": 0.0,
            "sugars": 0.0,
            "salt": 0.0,
        },
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(empty_log, f, ensure_ascii=False, indent=2)

    return {
        "status": "success",
        "message": f"Dziennik na dzień {date_str} został zresetowany do stanu początkowego.",
    }


# ============================================================================
# TOOL SCHEMAS FOR OPENAI / GEMINI FUNCTION CALLING
# ============================================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_daily_log",
            "description": "Fetch the current daily meal entries and macronutrient totals for a given date (read-only).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Date in DD-MM-YYYY format, e.g., '23-10-2026'.",
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
            "description": "Query the local JSON database for an ingredient and its nutritional values (read-only).",
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
            "description": "Search public Open Food Facts database when an item is missing locally (read-only).",
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
            "description": "Save an item discovered via Open Food Facts into the local database (reversible write).",
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
                            "sugars": {"type": "number"},
                            "salt": {"type": "number"},
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
            "name": "log_meal_entry",
            "description": "Append a new structured meal entry with items and nutritional values into the daily log (reversible write).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Date formatted as DD-MM-YYYY.",
                    },
                    "meal_name": {
                        "type": "string",
                        "description": "Title of the meal, e.g. 'Obiad: Kurczak z ryżem'.",
                    },
                    "items": {
                        "type": "array",
                        "description": "List of food items composing the meal with scaled macro values.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "amount_g": {"type": "number"},
                                "calories": {"type": "number"},
                                "protein": {"type": "number"},
                                "fats": {"type": "number"},
                                "carbs": {"type": "number"},
                                "sugars": {"type": "number"},
                                "salt": {"type": "number"},
                            },
                            "required": ["name", "amount_g", "calories", "protein", "fats", "carbs"],
                        },
                    },
                },
                "required": ["date_str", "meal_name", "items"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_daily_log",
            "description": "Persist the daily log with full structure and updated cumulative totals (reversible write).",
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
    {
        "type": "function",
        "function": {
            "name": "delete_meal_entry",
            "description": "Permanently delete a meal by its meal_id or name from the daily log (SENSITIVE - requires human confirmation).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Date formatted as DD-MM-YYYY.",
                    },
                    "meal_id": {
                        "type": "string",
                        "description": "Unique identifier of the meal to delete (e.g. 'meal_1732145000_01') or exact meal name.",
                    },
                },
                "required": ["date_str", "meal_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_daily_log",
            "description": "Clear all meal records and reset totals to 0 for a given date (SENSITIVE - requires human confirmation).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "Date formatted as DD-MM-YYYY.",
                    }
                },
                "required": ["date_str"],
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
    "log_meal_entry": log_meal_entry,
    "save_daily_log": save_daily_log,
    "delete_meal_entry": delete_meal_entry,
    "clear_daily_log": clear_daily_log,
}
