"""Bounded agent loop implementing tool execution and termination safeguards."""

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List
from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from agent_project.tools import TOOL_DEFINITIONS, TOOL_REGISTRY
from agent_project.tracing import calculate_cost, init_trace_file, record_step

API_KEY = os.getenv("MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or "mock-key"
BASE_URL = os.getenv("MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL")
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.6-flash")

MAX_STEPS = 7


def build_system_prompt() -> str:
    """Build the system prompt dynamically injecting current system date."""
    today_str = datetime.now().strftime("%d-%m-%Y")
    return f"""You are MacroTrack: a nutritional inspection agent.
Today's system date is: {today_str}.
Rules:
1. Always respond to the user in Polish.
2. Unless the user explicitly specifies another date, assume the target date is today ({today_str}).
3. Read the daily log first with 'read_daily_log' using the target date (DD-MM-YYYY) to get previous meal records and targets.
4. Look up all requested ingredients using 'lookup_local_food'.
5. If an ingredient is missing locally, check 'query_open_food_facts' once. If not found or API returns only flavored variants, use standard nutritional estimates for the plain item (e.g., skyr naturalny: ~65 kcal, 12g protein per 100g) and proceed without endless searching.
6. Calculate proportional macro values for each food item according to specified weights.
7. In your final response:
   - Podsumuj wartości dodawanego posiłku (kcal, białko, tłuszcze, węglowodany, cukry, sól).
   - Przedstaw nowy łączny bilans dnia (poprzednie posiłki + nowy).
   - Wskaż, ile kalorii pozostało do zrealizowania dziennego celu (lub o ile cel został przekroczony)."""


def execute_tool_call(tool_name: str, raw_args: str) -> Dict[str, Any]:
    """Execute a registered read-only tool with error handling."""
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Narzędzie '{tool_name}' nie istnieje."}

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as err:
        return {"error": f"Błąd parsowania argumentów: {str(err)}"}

    try:
        result = TOOL_REGISTRY[tool_name](**args)
        return {"result": result}
    except Exception as exc:
        return {"error": f"Błąd wykonania narzędzia: {str(exc)}"}


def _call_model_with_retry(client: OpenAI, **kwargs: Any) -> Any:
    """Invoke chat completion with retries for transient 503/429 errors."""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_err = exc
            err_str = str(exc)
            if "503" in err_str or "429" in err_str:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last_err or RuntimeError("Nieznany błąd wywołania API.")


def run_agent_loop(user_query: str) -> str:
    """Run the bounded ReAct cycle."""
    init_trace_file()

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    system_prompt = build_system_prompt()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query},
    ]

    print(f"[Agent] Zapytanie: {user_query}")
    print(f"[Agent] Maksymalny limit kroków: {MAX_STEPS}\n" + "-" * 50)

    for step in range(1, MAX_STEPS + 1):
        step_start = time.time()
        print(f"\n[Krok {step}/{MAX_STEPS}] Model analizuje stan...")

        response = _call_model_with_retry(
            client,
            model=MODEL_NAME,
            messages=messages,
            tools=TOOL_DEFINITIONS,
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

        # Wykonanie narzędzi i zwrot wyników do modelu
        messages.append(message)
        trace_entry["tool_executions"] = []

        for call in message.tool_calls:
            fn_name = call.function.name
            fn_args = call.function.arguments
            print(f"  -> Wywołanie narzędzia: {fn_name}({fn_args})")

            tool_output = execute_tool_call(fn_name, fn_args)

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
        "status": "STOPPED_MAX_STEPS",
        "message": fallback_message,
    })
    return fallback_message


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        print("Wpisz co zjadłeś (lub naciśnij Enter, aby użyć domyślnego prompta):")
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