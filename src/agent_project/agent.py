"""Bounded agent loop implementing tool execution, security policy, and termination safeguards."""

from __future__ import annotations
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, List, Optional
from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from agent_project.policy import execute_tool_with_policy
from agent_project.tools import TOOL_DEFINITIONS
from agent_project.tracing import calculate_cost, init_trace_file, record_step

API_KEY = os.getenv("MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or "mock-key"
BASE_URL = os.getenv("MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")

MAX_STEPS = 7


def build_system_prompt() -> str:
    """Build the system prompt dynamically injecting current system date and tool guidelines."""
    today_str = datetime.now().strftime("%d-%m-%Y")
    return f"""You are MacroTrack: an autonomous nutritional and fitness inspection agent.
Today's system date is: {today_str}.

Available tool types and operational guidelines:
1. READ-ONLY tools:
   - 'read_daily_log': Check previous meal entries and macro totals for target date (DD-MM-YYYY).
   - 'lookup_local_food': Query local nutrition database for ingredients.
   - 'query_open_food_facts': Search fallback database if ingredient is not found locally.
   - 'estimate_burned_calories': Calculate calories burned for a physical activity using MET standards.
   - 'get_daily_workouts': View all logged exercises and total burned calories for the date.

2. REVERSIBLE WRITE tools:
   - 'cache_food_item': Save newly resolved product to local database for future reuse.
   - 'log_meal_entry': Append a new structured meal entry to daily log.
   - 'log_workout': Record a completed exercise session in workouts log.
   - 'save_daily_log': Persist full updated daily log.

3. SENSITIVE tools (Strictly guarded by Python security policy):
   - 'delete_meal_entry': Delete a meal from the daily log by meal_id. Requires explicit human confirmation.
   - 'clear_daily_log': Reset all meals for the day. Requires explicit human confirmation.
   * If an action is denied by the user, observe the refusal and adjust your plan gracefully.

Execution Rules:
1. Always respond to the user in Polish.
2. Unless user specifies another date, assume target date is today ({today_str}).
3. Always inspect current state first with 'read_daily_log' before modifying or reporting totals.
4. If an ingredient is missing, query Open Food Facts or estimate realistic values, and cache it with 'cache_food_item'.
5. When adding meals, use 'log_meal_entry' or 'save_daily_log'.
6. If the user asks to delete or clear data, call the corresponding sensitive tool. The Python environment will request human confirmation. If confirmation is denied, inform the user and leave the data unchanged.
7. In your final response:
   - Podsumuj operacje (dodane/usunięte posiłki lub treningi).
   - Przedstaw aktualny bilans dnia (spożyte kalorie, makroskładniki oraz ewentualne spalone kalorie).
   - Wskaż bilans netto lub realizację dziennego limitu."""


def execute_tool_call(
    tool_name: str,
    raw_args: str,
    confirm_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """Execute a registered tool passing through the Python security and validation policy."""
    return execute_tool_with_policy(
        tool_name=tool_name,
        raw_args_str=raw_args,
        confirm_handler=confirm_handler,
    )


def _call_model_with_retry(client: OpenAI, **kwargs: Any) -> Any:
    """Invoke chat completion with retries for transient 503/429 errors."""
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_err = exc
            err_str = str(exc)
            if "503" in err_str or "429" in err_str:
                wait_time = 15 if "429" in err_str else 3 * (attempt + 1)
                print(f"  [API Retry] Limit zapytań / przeciążenie API. Oczekiwanie {wait_time}s (próba {attempt + 1}/4)...")
                time.sleep(wait_time)
                continue
            raise
    raise last_err or RuntimeError("Nieznany błąd wywołania API.")


def run_agent_loop(
    user_query: str,
    confirm_handler: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    custom_tools: Optional[List[Dict[str, Any]]] = None,
    hub: Optional[Any] = None,
) -> str:
    """Run the bounded ReAct cycle with policy protection and JSONL tracing."""
    init_trace_file()

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    system_prompt = build_system_prompt()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]

    if hub is not None:
        import anyio
        tools_to_use = anyio.run(hub.initialize_tools)
    elif custom_tools is not None:
        tools_to_use = custom_tools
    else:
        tools_to_use = TOOL_DEFINITIONS

    print(f"[Agent] Zapytanie: {user_query}")
    print(f"[Agent] Maksymalny limit kroków: {MAX_STEPS}\n" + "-" * 50)

    for step in range(1, MAX_STEPS + 1):
        step_start = time.time()
        print(f"\n[Krok {step}/{MAX_STEPS}] Model analizuje stan...")

        response = _call_model_with_retry(
            client,
            model=MODEL_NAME,
            messages=messages,
            tools=tools_to_use,
            tool_choice="auto",
        )

        delay = round(time.time() - step_start, 3)
        message = response.choices[0].message
        usage = response.usage

        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        cost = calculate_cost(prompt_tokens, completion_tokens)

        trace_entry: Dict[str, Any] = {
            "step": step,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model_provider": os.getenv("MODEL_PROVIDER", "google-ai-studio"),
            "model": MODEL_NAME,
            "response_time_seconds": delay,
            "delay_seconds": delay,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
            "estimated_cost_usd": cost,
            "decision": {
                "has_tool_calls": bool(message.tool_calls),
                "content": message.content,
            },
        }

        # Warunek stopu 1: Model wygenerował ostateczną odpowiedź (koniec zadania)
        if not message.tool_calls:
            print(f"[Krok {step}] Cel osiągnięty – model zakończył pętlę.")
            trace_entry["status"] = "COMPLETED"
            record_step(trace_entry)
            return message.content or "Brak podsumowania."

        # Wykonanie narzędzi pod nadzorem polityki i zwrot wyników do modelu
        messages.append(message)
        trace_entry["tool_executions"] = []

        for call in message.tool_calls:
            fn_name = call.function.name
            fn_args = call.function.arguments
            print(f"  -> Żądanie modelu: {fn_name}({fn_args})")

            if hub is not None:
                import anyio
                tool_output = anyio.run(hub.call_tool, fn_name, fn_args, confirm_handler)
            else:
                tool_output = execute_tool_call(fn_name, fn_args, confirm_handler=confirm_handler)

            trace_entry["tool_executions"].append({
                "tool_name": fn_name,
                "arguments": fn_args,
                "output": tool_output,
            })

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(tool_output, ensure_ascii=False),
            })

        trace_entry["status"] = "IN_PROGRESS"
        record_step(trace_entry)

    # Warunek stopu 2: Osiągnięcie limitu kroków
    fallback_message = f"Zatrzymano: osiągnięto limit {MAX_STEPS} kroków bez finalizacji."
    record_step({
        "step": MAX_STEPS + 1,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_provider": os.getenv("MODEL_PROVIDER", "google-ai-studio"),
        "model": MODEL_NAME,
        "status": "STOPPED_MAX_STEPS",
        "message": fallback_message,
    })
    return fallback_message


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        print("Wpisz zapytanie (lub naciśnij Enter, aby użyć domyślnego):")
        try:
            user_input = input("> ").strip()
        except EOFError:
            user_input = ""
        if user_input:
            prompt = user_input
        else:
            prompt = "Zjadłem 200g piersi z kurczaka i 150g skyru naturalnego. Sprawdź mój dziennik i podaj bilans."

    result = run_agent_loop(prompt)
    print("\n" + "=" * 50)
    print("WYNIK KOŃCOWY DLA UŻYTKOWNIKA:\n")
    print(result)
