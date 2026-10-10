"""Bounded agent loop implementing tool execution and termination safeguards."""

from __future__ import annotations
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List
from dotenv import load_dotenv
from openai import OpenAI

from agent_project.tools import TOOL_DEFINITIONS, TOOL_REGISTRY
from agent_project.tracing import calculate_cost, init_trace_file, record_step

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

API_KEY = os.getenv("OPENAI_API_KEY", "mock-key")
BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

MAX_STEPS = 5

SYSTEM_PROMPT = """You are MacroTrack: a nutritional inspection agent.
Rules:
1. Always respond to the user in Polish.
2. Read the daily log first with 'read_daily_log' to get previous meal records and targets.
3. Look up all requested ingredients using 'lookup_food_item'.
4. Calculate proportional macro values for each food item according to specified weights.
5. In your final response:
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


def run_agent_loop(user_query: str) -> str:
    """Run the bounded ReAct cycle."""
    init_trace_file()

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    print(f"[Agent] Zapytanie: {user_query}")
    print(f"[Agent] Maksymalny limit kroków: {MAX_STEPS}\n" + "-" * 50)

    for step in range(1, MAX_STEPS + 1):
        step_start = time.time()
        print(f"\n[Krok {step}/{MAX_STEPS}] Model analizuje stan...")

        response = client.chat.completions.create(
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
    prompt = (
        "Dziś jest 10-10-2026. Zjadłem właśnie 200g piersi z kurczaka i 150g skyru naturalnego. "
        "Sprawdź co już jadłem, wyszukaj wartości i podaj zaktualizowany bilans oraz ile kalorii "
        "zostało mi do dziennego limitu."
    )
    result = run_agent_loop(prompt)
    print("\n" + "=" * 50)
    print("WYNIK KOŃCOWY DLA UŻYTKOWNIKA:\n")
    print(result)