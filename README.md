# MacroTrack: Bounded Nutrition & Macro Logger Agent

## 1. Project Overview
MacroTrack is a lightweight, autonomous CLI agent designed to eliminate the friction of manually logging meals and macronutrients (protein, fats, carbohydrates, and calories). Instead of clicking through commercial apps, the user provides a natural language description of what they ate (e.g., *"I just ate 200g of chicken breast and 50g of rice"*), and the agent autonomously:
1. Reads the current daily log file for the specified date (`DD-MM-YYYY.json`).
2. Checks macronutrient values against a local reference database.
3. If an item is missing locally, queries the public **Open Food Facts API** as a fallback.
4. Calculates proportional values based on serving size, adds them to the cumulative daily totals, and writes the updated log.
5. Emits a structured audit trace of every reasoning step, tool call, token cost, and execution latency to `trace.jsonl`.

This workflow deliberately requires a multi-step agentic loop: tool outputs (such as a cache miss on an ingredient or existing totals in the daily log) directly determine the agent's next action.

---

## 2. Target User & Example Requests

* **Target User:** Individuals tracking calorie and macronutrient intake who want to log food using one-line natural language commands in a terminal without navigating complex mobile interfaces.
* **Example Prompts:**
  > *"Today is 10-10-2026. For lunch I had 200g of chicken breast, 80g of basmati rice, and 15g of olive oil. Check my existing log and update my totals."*  
  > *"I just had 150g of natural skyr. Add it to today."* (where skyr is absent from the local database and must be fetched from the API).

---

## 3. Success Criteria (Definition of Done)

A run is considered successful when all of the following conditions are met:
1. **Multi-Step Grounding:** The model does not hallucinate nutritional values; it retrieves them exclusively from the local database or the external API.
2. **State Continuity & Accumulation:** New meals are appended to the `meals` array, and `daily_totals` in `logs/DD-MM-YYYY.json` accurately reflects the cumulative sum of prior entries plus the new meal.
3. **Fallback Handling (Cache Miss):** When an ingredient is missing from the local file, the agent does not fail; it queries Open Food Facts and caches the newly fetched item in `data/nutrition_db.json`.
4. **Bounded Execution:** The agent loop is strictly constrained by a hard step limit (`MAX_STEPS = 6`). If an unresolvable issue occurs, it halts with a descriptive error.
5. **Deterministic Auditing:** Every decision loop iteration appends a record to `trace.jsonl` detailing latency, token counts, estimated USD cost, and tool inputs/outputs.

---

## 4. Decision Loop Architecture

The agent follows the ReAct pattern (Reason --> Act --> Observe):

```text
[User Prompt]
      │
      ▼
┌──────────────┐      Tool Call: read_daily_log
│  Agent Loop  │ ───────────────────────────────────► Fetch current daily totals
└──────────────┘ ◄─────────────────────────────────── (Returns existing totals or zeroed template)
      │
      ▼
┌──────────────┐      Tool Call: lookup_local_food
│  Agent Loop  │ ───────────────────────────────────► Query local JSON database
└──────────────┘ ◄───────────────────────────────────
      │
      ├── [Item Found] ─────────────────┐
      │                                 │
      └── [Cache Miss / Not Found]      │
               │                        │
               ▼                        │
         Tool Call:                     │
         query_open_food_facts          │
               │                        │
               ▼                        │
┌──────────────┐                        ▼
│  Agent Loop  │ ◄─── Retrieved data from API / Local DB
└──────────────┘
      │
      ▼
┌──────────────┐      Tool Call: save_daily_log
│  Agent Loop  │ ───────────────────────────────────► Scale portions, aggregate macros,
└──────────────┘ ◄─────────────────────────────────── and save to logs/DD-MM-YYYY.json
      │
      ▼
[Final Response] (Meal breakdown + updated daily totals for the user)