"""Security and control policy for tool execution.

Implements the required behavioral constraints from course specifications:
- Dispatches only allowed tools registered in code.
- Strictly validates argument schemas using Pydantic.
- Enforces an interactive human confirmation gate for sensitive operations (delete, clear, overwrite).
- Returns structured observation dictionaries so the model can observe denial and adapt its plan.
"""

from __future__ import annotations
from enum import Enum
import json
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, ValidationError

from agent_project.tools import TOOL_REGISTRY


class ToolCategory(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    SENSITIVE = "sensitive"


# Map each tool to its architectural category
TOOL_POLICY_MAP: Dict[str, ToolCategory] = {
    # Read-only
    "read_daily_log": ToolCategory.READ_ONLY,
    "lookup_local_food": ToolCategory.READ_ONLY,
    "query_open_food_facts": ToolCategory.READ_ONLY,
    # Reversible write
    "cache_food_item": ToolCategory.REVERSIBLE_WRITE,
    "log_meal_entry": ToolCategory.REVERSIBLE_WRITE,
    "save_daily_log": ToolCategory.REVERSIBLE_WRITE,
    # Sensitive (requires human confirmation)
    "delete_meal_entry": ToolCategory.SENSITIVE,
    "clear_daily_log": ToolCategory.SENSITIVE,
}


# ============================================================================
# PYDANTIC ARGUMENT SCHEMAS (Strict typing and validation)
# ============================================================================

class ReadDailyLogArgs(BaseModel):
    date_str: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")


class LookupLocalFoodArgs(BaseModel):
    query: str = Field(..., min_length=1)


class QueryOpenFoodFactsArgs(BaseModel):
    product_name: str = Field(..., min_length=1)


class FoodItemData(BaseModel):
    name: str = Field(..., min_length=1)
    unit: str = Field(default="100g")
    calories: float = Field(..., ge=0.0)
    protein: float = Field(..., ge=0.0)
    fats: float = Field(..., ge=0.0)
    carbs: float = Field(..., ge=0.0)
    sugars: Optional[float] = Field(default=0.0, ge=0.0)
    salt: Optional[float] = Field(default=0.0, ge=0.0)


class CacheFoodItemArgs(BaseModel):
    item_data: FoodItemData


class MealItem(BaseModel):
    name: str = Field(..., min_length=1)
    amount_g: float = Field(..., gt=0.0)
    calories: float = Field(..., ge=0.0)
    protein: float = Field(..., ge=0.0)
    fats: float = Field(..., ge=0.0)
    carbs: float = Field(..., ge=0.0)
    sugars: Optional[float] = Field(default=0.0, ge=0.0)
    salt: Optional[float] = Field(default=0.0, ge=0.0)


class LogMealEntryArgs(BaseModel):
    date_str: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")
    meal_name: str = Field(..., min_length=1)
    items: List[MealItem] = Field(..., min_length=1)


class SaveDailyLogArgs(BaseModel):
    date_str: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")
    updated_log: Dict[str, Any]


class DeleteMealEntryArgs(BaseModel):
    date_str: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")
    meal_id: str = Field(..., min_length=1)


class ClearDailyLogArgs(BaseModel):
    date_str: str = Field(..., pattern=r"^\d{2}-\d{2}-\d{4}$")


ARGUMENT_VALIDATORS: Dict[str, type[BaseModel]] = {
    "read_daily_log": ReadDailyLogArgs,
    "lookup_local_food": LookupLocalFoodArgs,
    "query_open_food_facts": QueryOpenFoodFactsArgs,
    "cache_food_item": CacheFoodItemArgs,
    "log_meal_entry": LogMealEntryArgs,
    "save_daily_log": SaveDailyLogArgs,
    "delete_meal_entry": DeleteMealEntryArgs,
    "clear_daily_log": ClearDailyLogArgs,
}


# ============================================================================
# POLICY CONTROL FUNCTIONS
# ============================================================================

def is_tool_allowed(tool_name: str) -> bool:
    """Check if the requested tool exists in the authorized registry."""
    return tool_name in TOOL_REGISTRY and tool_name in TOOL_POLICY_MAP


def get_tool_category(tool_name: str) -> Optional[ToolCategory]:
    """Retrieve the architectural risk category for a tool."""
    return TOOL_POLICY_MAP.get(tool_name)


def validate_arguments(tool_name: str, raw_args: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Validate arguments against Pydantic schema.
    Returns (is_valid, validated_dict, error_message).
    """
    validator = ARGUMENT_VALIDATORS.get(tool_name)
    if not validator:
        return True, raw_args, None

    try:
        model_inst = validator(**raw_args)
        return True, model_inst.model_dump(), None
    except ValidationError as err:
        return False, None, f"Błąd walidacji argumentów dla '{tool_name}': {str(err)}"


def default_human_confirmation_prompt(tool_name: str, args: Dict[str, Any]) -> bool:
    """Default interactive CLI prompt asking the user for confirmation on sensitive actions."""
    print("\n" + "=" * 50)
    print(" [BEZPIECZEŃSTWO] WYKRYTO OPERACJĘ WRAŻLIWĄ (SENSITIVE ACTION)")
    print(f" Narzędzie : {tool_name}")
    print(f" Argumenty : {json.dumps(args, ensure_ascii=False, indent=2)}")
    print("=" * 50)

    try:
        answer = input(" Czy wyrażasz zgodę na wykonanie tej operacji? [t/n]: ").strip().lower()
        return answer in ("t", "tak", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def execute_tool_with_policy(
    tool_name: str,
    raw_args_str: str,
    confirm_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """Execute a requested tool call passing through Python security and validation gates.

    Control steps:
    1. Check if tool is allowed in registry.
    2. Parse and validate arguments strictly against Pydantic schema.
    3. Check tool risk category. If SENSITIVE, invoke human confirmation gate.
    4. Execute authorized action and return structured result.
    """
    # 1. Authorization check
    if not is_tool_allowed(tool_name):
        err_msg = f"Narzędzie '{tool_name}' nie jest zarejestrowane ani dozwolone w polityce."
        return {
            "status": "error",
            "error": err_msg,
            "error_type": "UNAUTHORIZED_TOOL",
            "message": err_msg,
        }

    # 2. Argument decoding and Pydantic validation
    try:
        parsed_args = json.loads(raw_args_str) if raw_args_str else {}
    except json.JSONDecodeError as err:
        err_msg = f"Błąd dekodowania JSON: {str(err)}"
        return {
            "status": "error",
            "error": err_msg,
            "error_type": "MALFORMED_JSON",
            "message": err_msg,
        }

    is_valid, validated_args, val_error = validate_arguments(tool_name, parsed_args)
    if not is_valid or validated_args is None:
        return {
            "status": "error",
            "error": val_error or "Błąd walidacji",
            "error_type": "VALIDATION_FAILED",
            "message": val_error,
        }

    # 3. Confirmation gate for Sensitive tools
    category = get_tool_category(tool_name)
    if category == ToolCategory.SENSITIVE:
        prompt_fn = confirm_handler or default_human_confirmation_prompt
        confirmed = prompt_fn(tool_name, validated_args)
        if not confirmed:
            return {
                "status": "confirmation_denied",
                "tool_name": tool_name,
                "message": (
                    f"Użytkownik odmówił zgody na wykonanie operacji wrażliwej '{tool_name}'. "
                    "Operacja została zablokowana przez politykę aplikacji. "
                    "Poinformuj użytkownika o anulowaniu akcji i zachowaj aktualny stan."
                ),
            }

    # 4. Safe execution
    try:
        func = TOOL_REGISTRY[tool_name]
        result = func(**validated_args)
        return {
            "status": "success",
            "result": result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error_type": "EXECUTION_EXCEPTION",
            "message": f"Błąd wykonania funkcji '{tool_name}': {str(exc)}",
        }
