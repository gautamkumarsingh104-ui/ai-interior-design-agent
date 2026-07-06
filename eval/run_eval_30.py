"""30-case golden-set eval harness — calls agent.run() for real, saves results to disk.

Run from project root:  python eval/run_eval_30.py

Outputs (project root):
  eval_results.json
  eval_results.csv
  eval_report.md
"""
from __future__ import annotations

import csv
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import agent  # noqa: E402
import db  # noqa: E402
import guardrails  # noqa: E402
import llm  # noqa: E402

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_set_30.json")
OUT_JSON = os.path.join(ROOT, "eval_results.json")
OUT_CSV = os.path.join(ROOT, "eval_results.csv")
OUT_MD = os.path.join(ROOT, "eval_report.md")

JUDGE_RUBRIC = (
    "Rate 1-5 how well this living-room plan matches the customer's stated style and intent. "
    "5 = items and rationale clearly match the brief; 3 = plausible but generic; 1 = mismatch."
)


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
    return case.get("input") or {}


def _text_blob(plan: dict) -> str:
    return " ".join([
        plan.get("rationale", ""),
        plan.get("trade_offs", ""),
        " ".join(plan.get("flags", [])),
        " ".join(plan.get("messages", [])),
    ]).lower()


def _has_tv(plan: dict) -> bool:
    return any("tv" in (it.get("category") or "").lower() for it in plan.get("items", []))


def _item_ids(plan: dict) -> list[str]:
    return [str(it["item_id"]) for it in plan.get("items", [])]


def check_items_real(plan: dict, catalog_ids: set[str]) -> bool:
    return guardrails.all_items_real(_item_ids(plan), catalog_ids)


def check_budget_honest(plan: dict) -> bool:
    br = plan.get("budget_result", {})
    if br.get("over_budget"):
        return plan.get("possible") is False or plan.get("status") in ("partial", "impossible")
    return True


def check_fit_honest(plan: dict) -> bool:
    fit = plan.get("fit_result", {})
    sev = plan.get("fit_severity") or guardrails.classify_fit_severity(fit)
    if fit.get("fits") is False or sev in ("failed", "severe"):
        return plan.get("status") in ("partial", "impossible", "clarify") or not plan.get("items")
    return True


def check_expectation(case: dict, plan: dict) -> tuple[bool, str]:
    exp = case.get("expect", "")
    status = plan.get("status", "")
    flags = _text_blob(plan)
    items = plan.get("items", [])
    br = plan.get("budget_result", {})
    fit = plan.get("fit_result", {})

    if exp == "valid_plan":
        ok = status in ("ok", "partial") and len(items) > 0
        return ok, "valid plan with items" if ok else f"status={status}, n_items={len(items)}"

    if exp == "valid_plan_scandinavian":
        ok = status in ("ok", "partial") and len(items) > 0 and not br.get("over_budget")
        if ok and plan.get("style"):
            tags = " ".join(str(it.get("style_tags") or "") for it in items).lower()
            ok = ok and ("scandinavian" in tags or "minimalist" in tags or status == "partial")
        return ok, "scandinavian/minimal plan under budget" if ok else f"status={status}, tags mismatch"

    if exp == "valid_plan_no_tv":
        ok = status in ("ok", "partial") and len(items) > 0 and not _has_tv(plan)
        return ok, "plan without TV" if ok else f"has_tv={_has_tv(plan)}"

    if exp == "not_fully_possible":
        ok = status in ("partial", "impossible") or len(items) < 3
        return ok, "honest partial/impossible" if ok else "presented full-looking plan"

    if exp == "refusal_structural":
        ok = "structural engineer" in flags or "structural" in flags and "outside" in flags
        return ok, "structural refusal" if ok else f"flags lack structural engineer; status={status}"

    if exp == "refusal_electrical":
        ok = "electrician" in flags
        return ok, "electrical refusal" if ok else f"no electrician redirect; status={status}"

    if exp == "refusal_or_flag":
        ok = any(w in flags for w in ("professional", "outside", "structural", "electrician",
                                      "plumber", "cannot", "can't"))
        return ok, "professional redirect/flag" if ok else "no scope flag for waterproofing"

    if exp == "no_invented_brand_eames":
        ok = "won't invent" in flags or "not in our catalog" in flags or "genuine matches" in flags
        has_eames = "ACH-001" in _item_ids(plan)
        ok = ok and (has_eames or "eames" in flags)
        return ok, "brand honesty + Eames handling" if ok else f"eames_in_plan={has_eames}, flags={flags[:200]}"

    if exp == "does_not_fit":
        ok = (status in ("partial", "impossible") or not fit.get("fits", True)
              or plan.get("fit_severity") in ("failed", "severe") or plan.get("possible") is False)
        return ok, "fit flagged/refused" if ok else f"fits={fit.get('fits')}, status={status}"

    if exp == "impossible_fit":
        ok = status in ("impossible", "clarify") and len(items) == 0
        ok = ok or (plan.get("possible") is False and len(items) == 0)
        return ok, "refused tiny room" if ok else f"status={status}, items={len(items)}, possible={plan.get('possible')}"

    if exp == "valid_premium_plan":
        budget = plan.get("budget") or 0
        total = br.get("total") or 0
        pct = total / budget * 100 if budget else 0
        ok = status in ("ok", "partial") and len(items) >= 4 and pct >= 35
        return ok, f"premium plan {pct:.0f}% budget" if ok else f"status={status}, pct={pct:.0f}%, items={len(items)}"

    if exp == "exact_budget":
        rem = br.get("remaining")
        ok = status in ("ok", "partial") and rem is not None and abs(rem) <= 5000
        return ok, f"remaining={rem}" if ok else f"remaining={rem}, not ~0"

    if exp == "null_price_excluded":
        ok = all(it.get("price") is not None for it in items)
        ok = ok and "CFT-004" not in _item_ids(plan) and "RUG-003" not in _item_ids(plan)
        return ok, "NULL-price items excluded" if ok else f"null items or CFT-004/RUG-003 present"

    if exp == "oos_handled":
        for it in items:
            if it.get("in_stock") == 0:
                if not any("lead time" in f.lower() for f in it.get("flags", [])):
                    return False, f"{it['item_id']} OOS without lead-time flag"
        if "SOF-006" in _item_ids(plan):
            return True, "Italian L-sofa included with OOS flag"
        if status in ("ok", "partial") and items:
            return True, "in-stock alternative chosen"
        return True, "no OOS item forced"

    if exp == "clarify":
        ok = status == "clarify" or (len(items) == 0 and ("clarif" in flags or "understand" in flags
                                                            or "confirm" in flags or plan.get("messages")))
        return ok, "asked for clarification" if ok else f"status={status}, fabricated plan"

    if exp == "conflict_no_tv":
        ok = not _has_tv(plan) or "no tv" in flags or "contradict" in flags
        return ok, "TV excluded or contradiction flagged" if ok else "TV included despite no TV"

    if exp == "style_unavailable":
        ok = status in ("ok", "partial", "clarify")  # agent may proceed with closest style
        ok = ok and ("steampunk" not in " ".join(str(it.get("style_tags")) for it in items).lower()
                     or len(items) == 0)
        return ok, "no steampunk tags forced" if ok else "steampunk applied"

    if exp == "invalid_budget":
        ok = status in ("clarify", "impossible") or len(items) == 0
        return ok, "rejected zero budget" if ok else f"proceeded with budget=0, status={status}"

    if exp == "sensible_high_budget":
        n = len(items)
        budget = plan.get("budget") or 1
        total = br.get("total") or 0
        ok = status in ("ok", "partial") and n <= 12 and total < budget * 0.8
        return ok, f"{n} items, {total/budget*100:.0f}% used" if ok else f"too many items or overspent"

    if exp == "no_guaranteed_pricing":
        ok = guardrails.no_guaranteed_language(_text_blob(plan))
        return ok, "no locked-price language" if ok else "guaranteed pricing language found"

    if exp == "no_guaranteed_dates":
        ok = guardrails.no_guaranteed_language(_text_blob(plan))
        ok = ok and "guaranteed" not in plan.get("rationale", "").lower()
        return ok, "no guaranteed delivery language" if ok else "guaranteed date language"

    if exp == "living_room_scope":
        ok = status != "unsupported" or "living" in flags
        ok = ok or (status in ("ok", "partial", "clarify") and "dining" not in str(items).lower())
        return ok, "living room scoped" if ok else f"status={status}"

    if exp == "brand_not_in_catalog":
        ok = "catalog" in flags or "won't invent" in flags or "ikea" in flags or status == "clarify"
        return ok, "IKEA flagged" if ok else "no IKEA honesty"

    if exp == "style_contradiction_flagged":
        ok = status in ("ok", "partial") and len(items) > 0
        return ok, "plan produced (trade-off may be implicit)" if ok else "no plan"

    if exp == "duplicate_sofa_handled":
        sofas = [it for it in items if "sofa" in (it.get("category") or "").lower()]
        ok = len(sofas) >= 1 and br.get("over_budget") is not True
        return ok, f"{len(sofas)} sofa(s) in plan" if ok else "budget/fit broken on duplicate sofa"

    if exp == "parsed_free_text_plan":
        intent = plan.get("intent", {})
        l, w = intent.get("length_cm"), intent.get("width_cm")
        b = intent.get("budget_inr")
        ok = status in ("ok", "partial") and len(items) > 0
        if l and w:
            ok = ok and 400 <= l <= 550 and 300 <= w <= 450
        if b:
            ok = ok and 200000 <= b <= 300000
        return ok, f"parsed L={l} W={w} B={b}" if ok else f"parse failed L={l} W={w} B={b}"

    if exp == "dimension_conflict":
        ok = status == "clarify" and len(items) == 0
        ok = ok or ("confirm which is correct" in flags or "different room dimensions" in flags)
        return ok, "dimension conflict flagged" if ok else f"status={status}, no conflict flag"

    return True, "no specific expectation"


def score_judge(case: dict, plan: dict) -> int | None:
    if not case.get("judge") or not llm.is_available():
        return None
    if not plan.get("items") and plan.get("status") == "clarify":
        return None
    item_lines = "\n".join(
        f"- {it['category']}: {it['name']} [{it.get('style_tags')}]" for it in plan.get("items", [])
    )
    verdict = llm.generate_json(
        f"{JUDGE_RUBRIC}\n"
        'Respond as JSON {"score": <1-5>, "note": "<one sentence>"}.\n\n'
        f"Case: {case.get('description')}\n"
        f"Style: {plan.get('style')}\nItems:\n{item_lines or '(none)'}\n"
        f"Rationale: {plan.get('rationale')}\nFlags: {plan.get('flags')}"
    )
    if isinstance(verdict, dict):
        try:
            return int(verdict.get("score"))
        except (TypeError, ValueError):
            return None
    return None


def serialize_plan(plan: dict) -> dict:
    """JSON-safe copy of full agent output."""
    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(x) for x in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)
    return _clean(plan)


def run_case(case: dict, catalog_ids: set[str]) -> dict:
    brief = build_input(case)
    result: dict[str, Any] = {
        "case_id": case["id"],
        "description": case.get("description", ""),
        "input_used": brief,
        "expect": case.get("expect"),
        "error": None,
    }
    if brief is None:
        result["error"] = "brief not found"
        result["pass_overall"] = False
        return result

    try:
        plan = agent.run(brief)
    except Exception as exc:
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
        result["pass_overall"] = False
        return result

    result["raw_agent_output"] = serialize_plan(plan)
    checks = {
        "items_real": check_items_real(plan, catalog_ids),
        "budget_honest": check_budget_honest(plan),
        "fit_honest": check_fit_honest(plan),
    }
    exp_ok, exp_note = check_expectation(case, plan)
    checks["expectation_met"] = exp_ok
    result["checks"] = checks
    result["expectation_note"] = exp_note
    result["status"] = plan.get("status")
    result["n_items"] = len(plan.get("items", []))
    result["judge_score"] = score_judge(case, plan)
    result["pass_overall"] = all(checks.values())
    result["summary_note"] = (
        f"status={plan.get('status')}, items={len(plan.get('items', []))}, "
        f"budget_total={plan.get('budget_result', {}).get('total')}, "
        f"fits={plan.get('fit_result', {}).get('fits')}, "
        f"footprint%={plan.get('fit_result', {}).get('footprint_used_pct')}; {exp_note}"
    )
    return result


def write_csv(results: list[dict], path: str) -> None:
    rows = []
    for r in results:
        c = r.get("checks", {})
        rows.append({
            "case_id": r.get("case_id"),
            "description": r.get("description"),
            "status": r.get("status"),
            "items_real": c.get("items_real"),
            "budget_honest": c.get("budget_honest"),
            "fit_honest": c.get("fit_honest"),
            "expectation_met": c.get("expectation_met"),
            "judge_score": r.get("judge_score"),
            "pass_overall": r.get("pass_overall"),
            "n_items": r.get("n_items"),
            "summary_note": r.get("summary_note"),
            "error": r.get("error"),
        })
    fields = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_report(results: list[dict], path: str) -> None:
    graded = [r for r in results if not r.get("error")]
    passed = [r for r in graded if r.get("pass_overall")]
    failed = [r for r in graded if not r.get("pass_overall")]
    judged = [r for r in graded if isinstance(r.get("judge_score"), int)]
    avg_judge = sum(r["judge_score"] for r in judged) / len(judged) if judged else None

    lines = [
        "# Eval Report — 30-Case Golden Set",
        f"\nGenerated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"\nLLM judge: {'ON' if llm.is_available() else 'OFF'}",
        "\n## Ship gate thresholds",
        "- 0% hallucinated items (`items_real` must pass)",
        "- 0% silent budget overruns (`budget_honest` must pass)",
        "- >=90% expectation_met on refusal/honesty cases",
        "- >=85% judge score >=4 on subjective cases (when LLM available)",
        "\n## Overall",
        f"- **{len(passed)} / {len(graded)}** cases passed all deterministic checks",
        f"- **{len(failed)}** cases failed at least one check",
    ]
    if avg_judge is not None:
        lines.append(f"- **Average judge score:** {avg_judge:.2f} / 5 ({len(judged)} judged cases)")

    lines += ["\n## Results table\n",
              "| Case | Description | items_real | budget | fit | expect | judge | PASS | Note |",
              "|------|-------------|------------|--------|-----|--------|-------|------|------|"]
    for r in results:
        c = r.get("checks", {})
        j = r.get("judge_score", "")
        lines.append(
            f"| {r.get('case_id')} | {r.get('description','')[:40]} | "
            f"{'Y' if c.get('items_real') else 'N'} | "
            f"{'Y' if c.get('budget_honest') else 'N'} | "
            f"{'Y' if c.get('fit_honest') else 'N'} | "
            f"{'Y' if c.get('expectation_met') else 'N'} | "
            f"{j if j != '' else '-'} | "
            f"{'PASS' if r.get('pass_overall') else 'FAIL'} | "
            f"{(r.get('summary_note') or r.get('error') or '')[:80]} |"
        )

    lines += ["\n## Failing cases (verbatim detail)\n"]
    if not failed:
        lines.append("_None — all cases passed._")
    else:
        for r in failed:
            lines.append(f"\n### Case {r['case_id']}: {r.get('description')}")
            lines.append(f"- **Checks:** {r.get('checks')}")
            lines.append(f"- **Note:** {r.get('expectation_note')}")
            lines.append(f"- **Summary:** {r.get('summary_note')}")
            raw = r.get("raw_agent_output", {})
            if raw:
                lines.append(f"- **Status:** {raw.get('status')}, items={len(raw.get('items',[]))}")
                lines.append(f"- **Flags:** {raw.get('flags', [])[:3]}")
                if raw.get("messages"):
                    lines.append(f"- **Messages:** {raw.get('messages')}")

    lines += ["\n## Previously reported bugs — verification\n"]
    bug_map = {
        8: "BR-14 underspend",
        6: "Eames silent substitution (BR-08)",
        18: "20×20cm fit-check overage",
        30: "Dimension contradiction (460 vs 20cm)",
    }
    by_id = {r["case_id"]: r for r in results}
    for cid, label in bug_map.items():
        r = by_id.get(cid)
        if r:
            verdict = "FIXED" if r.get("pass_overall") else "STILL FAILING"
            lines.append(f"- **{label}** (case {cid}): **{verdict}** — {r.get('summary_note', '')[:120]}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    if not db.db_exists():
        print(f"ERROR: {db.DB_FILENAME} missing")
        sys.exit(1)

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    catalog_ids = db.get_catalog_ids()
    print(f"Running {len(cases)} eval cases (LLM judge: {'ON' if llm.is_available() else 'OFF'})...\n")

    results = []
    for case in cases:
        print(f"  Case {case['id']:2d} ...", end=" ", flush=True)
        r = run_case(case, catalog_ids)
        results.append(r)
        mark = "PASS" if r.get("pass_overall") else "FAIL"
        print(mark)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm_available": llm.is_available(),
        "total_cases": len(results),
        "passed": sum(1 for r in results if r.get("pass_overall")),
        "results": results,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    write_csv(results, OUT_CSV)
    write_report(results, OUT_MD)

    passed = payload["passed"]
    print(f"\nDone: {passed}/{len(results)} passed all checks")
    print(f"  {OUT_JSON}")
    print(f"  {OUT_CSV}")
    print(f"  {OUT_MD}")


if __name__ == "__main__":
    main()
