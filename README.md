# AI Interior Design Agent — Living Room MVP

An agentic assistant that turns a living-room brief (structured form and/or free
text) into a **Design Plan + itemized BOQ** using only **real catalog items**,
within budget, that physically fit the room — with honest trade-offs and hard
guardrails enforced in code.

Built for the Interior Company x Blocks APM Build Challenge. Scope: **Living Room only**.

---

## Quick start (under 5 minutes)

1. **Get the code and the database**
   - Place the provided `interior_company_catalog.db` in the project root
     (next to `app.py`). It is read-only and never modified.

2. **Create a virtual environment and install dependencies**

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

3. **Set your Gemini API key** (create a `.env` file in the project root)

```
GEMINI_API_KEY=your_key_here
# optional: GEMINI_MODEL=gemini-2.5-flash
```

> No key? The app still runs using a deterministic fallback (rule-based planning
> and rationale). The LLM-as-judge eval scorer is skipped without a key.

4. **Run the app**

```bash
streamlit run app.py
```

5. **Run the eval harness** (separate script, testing not training)

```bash
python eval/run_eval.py
```

Results are printed as a pass/fail table and written to `eval/results/results.csv`.

---

## How it works (agentic, multi-step)

```
Parse brief -> guardrail pre-checks -> LLM tool-calling loop (max 5)
            -> deterministic guardrail gate (repair budget & fit)
            -> rationale + trade-offs -> Design Plan card
```

- **LLM proposes, code decides.** Gemini orchestrates the plan via native
  function calls to three tools; deterministic Python then validates and repairs
  the plan (swap-on-fail for budget and room fit) and has the final veto.
- **Three tools** (`tools.py`), each logged to a per-run trace:
  - `catalog_search(category, style_tags, room_type, max_price, in_stock_only)`
  - `budget_calculator(selected_items, budget_inr)`
  - `fit_check(selected_items, room_length_cm, room_width_cm)` — area heuristic,
    not CAD.

## Guardrails (enforced in code — `guardrails.py`)

- Never invent items — every final `item_id` is checked against the catalog.
- Never silently exceed budget — `total <= budget` enforced; shortfall stated.
- Refuse structural / electrical / plumbing questions — redirect to the right pro.
- No fake designer/brand items — offer real substitutes, never rename.
- No guaranteed dates / locked prices — lead times are estimates only.
- Handle NULL price (excluded), NULL dimensions (flagged), out-of-stock (flagged).

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit single-page UI (form + free text + result card + trace) |
| `agent.py` | Orchestrator: parse, LLM tool loop, repair, rationale |
| `tools.py` | `catalog_search`, `budget_calculator`, `fit_check` |
| `guardrails.py` | Scope refusal, brand detection, code-level validators |
| `db.py` | Read-only SQLite access + schema verification |
| `llm.py` | Gemini (google-genai) client wrapper |
| `eval/golden_set.json` | 22 test cases (8 real briefs + 14 authored) |
| `eval/run_eval.py` | Deterministic + LLM-judge scorers, tool-use check, ship gate |
| `DECISION_LOG.md` | Scope, AI direction/overrides, production risks, next steps |

## Ship gate (checked by the eval harness)

- 0% hallucinated items
- 0% silent budget overruns
- >=90% correct refusals / honest flags on out-of-scope or impossible cases
- >=85% of judged cases rated >=4/5 on style coherence
