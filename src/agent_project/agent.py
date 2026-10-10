"""Bounded agent execution loop with token accounting and audit trace logging."""

from __future__ import annotations
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List
from openai import OpenAI

from tools import TOOL_DEFINITIONS, TOOL_REGISTRY

MODEL_NAME = "gpt-4o-mini"
MAX_STEPS = 6
TRACE_FILE = Path("trace.jsonl")

# Cost constants per 1M tokens (USD)
INPUT_COST_PER_M = 0.15
OUTPUT_COST_PER_M = 0.60

SYSTEM_PROMPT = """You are MacroTrack: a precise, autonomous nutrition logging agent.
Core operational directives:
1. Language requirement: Communicate with the user EXCLUSIVELY in Polish. Keep tone natural, concise, and professional.
2. Step 1: Always check the daily log first using 'read_daily_log' to retrieve prior meals and cumulative totals.
3. Step 2: Look up food items in the local database using 'lookup_local_food'.
4. Step 3 (Fallback): If an item is not found locally, query the external API via 'query_open_food_facts'. If found in the API, cache it using 'cache_food_item'.
5. Step 4: Accurately calculate nutritional values based on user-provided portion sizes.
6. Step 5: Append the new meal to 'meals' and mathematically accumulate all macronutrients into 'daily_totals'. Save the state with 'save_daily_log'.
7. Step 6: Conclude with a clear Polish summary detailing:
   - Co zostało dodane do posiłku (składniki, gramatura, makro),
   - Nowy łączny bilans dnia (kalorie, białko, tłuszcze, węglowodany)."""


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate token cost estimate in USD."""
    in_cost = (prompt_tokens / 1_000_000) * INPUT_COST_PER_M
    out_cost = (completion_tokens / 1_000_000) * OUTPUT_COST_PER_M
    return round(in_cost + out_cost, 6)


def record_trace(entry: Dict[str, Any]) -> None:
    """Append a step record into the JSONL trace file."""
    with open(TRACE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def execute_tool_call(tool_name: str, raw_args: str) -> Dict[str, Any]:
    """Execute a tool function from the registry with error handling."""
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Tool '{tool_name}' is not registered."}

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as err:
        return {"error": f"Invalid JSON arguments: {str(err)}"}

    try:
        result = TOOL_REGISTRY[tool_name](**args)
        return {"result": result}
    except Exception as exc:
        return {"error": f"Tool execution failed: {str(exc)}"}


def run_agent_loop(user_query: str) -> str:
    """Run the bounded ReAct cycle."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "mock-key"))

    if TRACE_FILE.exists():
        TRACE_FILE.unlink()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    print(f"[Agent Loop] Initiated for input: \"{user_query}\"")
    print(f"[Agent Loop] Bounded limit: {MAX_STEPS} steps\n" + "-" * 50)

    for step in range(1, MAX_STEPS + 1):
        step_start_time = time.time()
        print(f"\n[Step {step}/{MAX_STEPS}] Evaluating next action...")

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
        )

        delay = round(time.time() - step_start_time, 3)
        choice = response.choices[0]
        message = choice.message
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

        # Terminal state reached: model produced the final Polish answer
        if not message.tool_calls:
            print(f"[Step {step}] Terminal goal reached.")
            trace_entry["status"] = "COMPLETED"
            record_trace(trace_entry)
            return message.content or "Brak podsumowania."

        # Intermediate state: execute tool calls
        messages.append(message)
        trace_entry["tool_executions"] = []

        for call in message.tool_calls:
            fn_name = call.function.name
            fn_args = call.function.arguments
            print(f"  -> Invoking tool: {fn_name}({fn_args})")

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
        record_trace(trace_entry)

    # Step limit guardrail
    fallback_message = "Osiągnięto maksymalny limit kroków agenta bez zakończenia operacji."
    record_trace({
        "step": MAX_STEPS + 1,
        "status": "STOPPED_MAX_STEPS",
        "message": fallback_message,
    })
    return fallback_message


if __name__ == "__main__":
    sample_prompt = (
        "Dziś jest 10-10-2026. Na obiad wpadło: 200g piersi z kurczaka, "
        "80g ryżu basmati oraz 150g skyru naturalnego. Zaktualizuj mój bilans."
    )
    result = run_agent_loop(sample_prompt)
    print("\n" + "=" * 50)
    print("ODPOWIEDŹ DLA UŻYTKOWNIKA:\n")
    print(result)