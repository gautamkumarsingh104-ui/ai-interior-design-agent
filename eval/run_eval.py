"""Evaluation harness for the interior design agent.

This is TESTING, not training. The agent is already built and does not change
here. We call it once per golden-set case and grade each output with:
  - deterministic scorers  (pure code: budget, real items, fit, language, stock)
  - an LLM-as-judge scorer  (style coherence, 1-5)
  - a tool-use check        (were budget_calculator & fit_check actually called?)

Run from the project root:  python eval/run_eval.py
"""
from __future__ import annotations

import csv
import json
import os
import sys

# Allow running from the project root or the eval/ folder.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import agent  # noqa: E402
import db  # noqa: E402
import guardrails  # noqa: E402
import llm  # noqa: E402

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_set.json")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


# --- Input building -------------------------------------------------------

def build_input(case: dict) -> dict | None:
    if case.get("load_brief"):
        b = db.get_brief(case["load_brief"])
        if not b:
            return None
        return {
            "length_cm": b.get("length_cm"),
            "width_cm": b.get("width_cm"),
            "ceiling_cm": b.get("ceiling_cm"),
            "budget_inr": b.get("budget_inr"),
            "style_preference": b.get("style_preference"),
            "must_haves": b.get("must_haves"),
            "constraints": b.get("constraints"),
            "free_text": b.get("customer_note"),
        }
    return case.get("input", {})


# --- Deterministic scorers (universal invariants) ------------------------

def score_deterministic(plan: dict, catalog_ids: set[str]) -> dict:
    item_ids = [it["item_id"] for it in plan.get("items", [])]
    br = plan.get("budget_result", {})
    fit = plan.get("fit_result", {})
    text = f"{plan.get('rationale', '')}\n{plan.get('trade_offs', '')}\n" \
           + "\n".join(plan.get("flags", []))

    # Every out-of-stock item must carry a lead-time flag.
    oos_ok = True
    for it in plan.get("items", []):
        if it.get("in_stock") == 0:
            if not any("lead time" in f.lower() for f in it.get("flags", [])):
                oos_ok = False

    # No NULL-price item should ever appear in the final plan.
    null_price_ok = all(it.get("price") is not None for it in plan.get("items", []))

    return {
        "items_real": guardrails.all_items_real(item_ids, catalog_ids),
        "no_silent_overrun": not br.get("over_budget", False),
        "fit_ok_or_flagged": bool(fit.get("fits", True)) or plan.get("status") in ("partial", "impossible"),
        "no_guaranteed_language": guardrails.no_guaranteed_language(text),
        "out_of_stock_flagged": oos_ok,
        "no_null_price_items": null_price_ok,
    }


# --- Expectation-specific scorer -----------------------------------------

def _has_refusal(plan: dict) -> bool:
    text = " ".join(plan.get("flags", [])).lower()
    return any(w in text for w in ("structural engineer", "electrician", "plumber"))


def _has_tv(plan: dict) -> bool:
    return any("tv" in (it["category"] or "").lower() for it in plan.get("items", []))


def _has_brand_flag(plan: dict) -> bool:
    text = " ".join(plan.get("flags", [])).lower()
    return "catalog" in text and ("won't invent" in text or "alternative" in text
                                  or "not in our catalog" in text)


def score_expectation(case: dict, plan: dict) -> bool | None:
    expect = case.get("expect")
    status = plan.get("status")
    if expect == "valid_plan":
        return status in ("ok", "partial") and len(plan.get("items", [])) > 0
    if expect == "not_fully_possible":
        return status in ("partial", "impossible")
    if expect == "refusal":
        return _has_refusal(plan)
    if expect == "no_invented_brand":
        # Real items are guaranteed by items_real; also expect an honest brand note.
        return _has_brand_flag(plan) or status == "clarify"
    if expect == "does_not_fit":
        fit = plan.get("fit_result", {})
        return status in ("partial", "impossible") or not fit.get("fits", True) \
            or bool(plan.get("dropped"))
    if expect == "clarify":
        return status == "clarify"
    if expect == "handles_null_price":
        return all(it.get("price") is not None for it in plan.get("items", []))
    if expect == "out_of_stock_flag":
        for it in plan.get("items", []):
            if it.get("in_stock") == 0 and not any(
                    "lead time" in f.lower() for f in it.get("flags", [])):
                return False
        return True
    if expect == "conflict_no_tv":
        return not _has_tv(plan)
    return None


# --- Tool-use check -------------------------------------------------------

def score_tool_use(plan: dict) -> bool | None:
    if plan.get("status") == "clarify":
        return None  # no plan produced; tools legitimately not called
    tools_called = {t["tool"] for t in plan.get("tool_trace", [])}
    return "budget_calculator" in tools_called and "fit_check" in tools_called


# --- LLM-as-judge (style coherence) --------------------------------------

def score_style_judge(plan: dict) -> int | None:
    if not llm.is_available():
        return None
    if not plan.get("items") or not plan.get("style"):
        return None
    item_lines = "\n".join(
        f"- {it['category']}: {it['name']} [{it.get('style_tags')}]" for it in plan["items"]
    )
    verdict = llm.generate_json(
        "Rate 1-5 how well this design matches the requested style and brief. "
        "5 = every item matches the style and the rationale references brief details. "
        "3 = stylistically plausible but generic rationale. 1 = does not match. "
        'Respond as JSON {"score": <1-5>}.\n\n'
        f"Requested style: {plan['style']}\n"
        f"Items:\n{item_lines}\n"
        f"Rationale: {plan.get('rationale')}"
    )
    if isinstance(verdict, dict):
        try:
            return int(verdict.get("score"))
        except (TypeError, ValueError):
            return None
    return None


# --- Main -----------------------------------------------------------------

def main() -> None:
    if not db.db_exists():
        print(f"ERROR: {db.DB_FILENAME} not found in project root. Add it and re-run.")
        sys.exit(1)

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    catalog_ids = db.get_catalog_ids()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    rows: list[dict] = []
    print(f"Running {len(cases)} eval cases "
          f"(LLM {'ON' if llm.is_available() else 'OFF - deterministic only'})...\n")

    for case in cases:
        brief_input = build_input(case)
        if brief_input is None:
            print(f"  {case['id']}: SKIP (brief {case.get('load_brief')} not in DB)")
            rows.append({"id": case["id"], "expect": case.get("expect"),
                         "status": "skipped"})
            continue
        try:
            plan = agent.run(brief_input)
        except Exception as exc:
            print(f"  {case['id']}: ERROR {exc}")
            rows.append({"id": case["id"], "expect": case.get("expect"),
                         "status": "error", "error": str(exc)})
            continue

        det = score_deterministic(plan, catalog_ids)
        exp = score_expectation(case, plan)
        tool_use = score_tool_use(plan)
        judge = score_style_judge(plan)

        row = {
            "id": case["id"],
            "source": case.get("source"),
            "expect": case.get("expect"),
            "status": plan.get("status"),
            **det,
            "expectation_met": exp,
            "tool_use_ok": tool_use,
            "style_score": judge,
            "n_items": len(plan.get("items", [])),
            "n_tool_calls": len(plan.get("tool_trace", [])),
        }
        rows.append(row)

        det_pass = all(v for v in det.values())
        marks = "PASS" if det_pass and (exp is not False) else "FAIL"
        print(f"  {case['id']:<4} [{marks}] status={plan.get('status'):<10} "
              f"expect={case.get('expect'):<20} "
              f"det={'ok' if det_pass else 'X'} exp={exp} "
              f"tools={tool_use} style={judge}")

    _write_csv(rows)
    _report(rows)


def _write_csv(rows: list[dict]) -> None:
    path = os.path.join(RESULTS_DIR, "results.csv")
    fields = sorted({k for r in rows for k in r.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nResults written to {path}")


def _report(rows: list[dict]) -> None:
    graded = [r for r in rows if r.get("status") not in ("skipped", "error")]
    n = len(graded) or 1

    hallucinated = sum(1 for r in graded if r.get("items_real") is False)
    overruns = sum(1 for r in graded if r.get("no_silent_overrun") is False)

    honest_cases = [r for r in graded
                    if r.get("expect") in ("refusal", "not_fully_possible",
                                           "does_not_fit", "clarify", "no_invented_brand")]
    honest_ok = sum(1 for r in honest_cases if r.get("expectation_met") is True)

    judged = [r for r in graded if isinstance(r.get("style_score"), int)]
    style_ok = sum(1 for r in judged if r["style_score"] >= 4)

    tool_checked = [r for r in graded if r.get("tool_use_ok") is not None]
    tool_ok = sum(1 for r in tool_checked if r.get("tool_use_ok") is True)

    print("\n" + "=" * 60)
    print("SHIP GATE REPORT")
    print("=" * 60)
    print(f"Hallucinated items      : {hallucinated}/{n}  "
          f"(gate: 0)  -> {'PASS' if hallucinated == 0 else 'FAIL'}")
    print(f"Silent budget overruns  : {overruns}/{n}  "
          f"(gate: 0)  -> {'PASS' if overruns == 0 else 'FAIL'}")
    if honest_cases:
        pct = honest_ok / len(honest_cases) * 100
        print(f"Correct refusals/honesty: {honest_ok}/{len(honest_cases)} ({pct:.0f}%)  "
              f"(gate: >=90%)  -> {'PASS' if pct >= 90 else 'FAIL'}")
    if judged:
        pct = style_ok / len(judged) * 100
        print(f"Style coherence >=4/5   : {style_ok}/{len(judged)} ({pct:.0f}%)  "
              f"(gate: >=85%)  -> {'PASS' if pct >= 85 else 'FAIL'}")
    else:
        print("Style coherence         : N/A (no LLM key -> judge skipped)")
    if tool_checked:
        print(f"Tool-use (budget & fit) : {tool_ok}/{len(tool_checked)} used both tools")
    print("=" * 60)


if __name__ == "__main__":
    main()
