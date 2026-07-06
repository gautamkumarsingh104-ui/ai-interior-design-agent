"""Guardrails enforced in CODE, not merely prompted.

These are the hard constraints from the PRD. The LLM may propose anything; the
functions here decide what is actually allowed:
  - out-of-scope (structural / electrical / plumbing) requests are refused
  - named designer/brand items that do not exist are never invented
  - the final plan is validated (real items, budget, no guaranteed-date language)
"""
from __future__ import annotations

import re
from typing import Any

import db
import llm

# --- 1. Out-of-scope classification --------------------------------------

SCOPE_KEYWORDS = {
    "structural": [
        "load-bearing", "load bearing", "remove this wall", "remove a wall",
        "knock down", "knock through", "demolish", "beam", "foundation",
        "structural", "pillar", "column", "ceiling support",
    ],
    "electrical": [
        "rewire", "wiring", "electrical", "electrician", "circuit",
        "fuse box", "switchboard", "new socket", "add a socket", "voltage",
    ],
    "plumbing": [
        "plumbing", "plumber", "pipe", "drainage", "water line", "sewage",
        "move the sink", "reroute water",
    ],
}

REFERRAL = {
    "structural": "a qualified structural engineer",
    "electrical": "a licensed electrician",
    "plumbing": "a licensed plumber",
}


def classify_scope(text: str, use_llm: bool = True) -> dict[str, Any]:
    """Detect structural/electrical/plumbing intent. Keyword-first, LLM-confirm.

    Returns {out_of_scope, category, message}.
    """
    if not text:
        return {"out_of_scope": False, "category": None, "message": ""}
    low = text.lower()
    for category, words in SCOPE_KEYWORDS.items():
        if any(w in low for w in words):
            return _refusal(category)

    # LLM confirmation catches phrasings the keyword list misses.
    if use_llm and llm.is_available():
        verdict = llm.generate_json(
            "You are a scope classifier for an interior FURNISHING assistant. "
            "The assistant only selects furniture/decor. It must NOT advise on "
            "structural, electrical or plumbing work. "
            "Classify the user's request. Respond as JSON: "
            '{"out_of_scope": bool, "category": "structural|electrical|plumbing|null"}.\n\n'
            f"Request: {text}"
        )
        if isinstance(verdict, dict) and verdict.get("out_of_scope"):
            cat = verdict.get("category")
            if cat in REFERRAL:
                return _refusal(cat)
            return _refusal("structural")  # safe default referral
    return {"out_of_scope": False, "category": None, "message": ""}


def _refusal(category: str) -> dict[str, Any]:
    who = REFERRAL.get(category, "a qualified professional")
    return {
        "out_of_scope": True,
        "category": category,
        "message": (
            f"That part of your request involves {category} work, which is outside "
            f"what a furnishing assistant can safely advise on. Please consult {who} "
            f"for that. I can still help with furniture and decor below."
        ),
    }


# --- 2. Named brand / designer detection ---------------------------------

# Well-known design pieces/houses customers may name that are unlikely to be in
# a generic catalog. We never rename a real item to impersonate these.
KNOWN_BRANDS = [
    "togo", "noguchi", "eames", "herman miller", "b&b italia", "roche bobois",
    "ligne roset", "vitra", "knoll", "cassina", "minotti", "poliform",
    "west elm", "restoration hardware", "muuto", "hay", "fritz hansen",
    "le corbusier", "barcelona chair", "wishbone chair", "flos", "artek",
]


def detect_named_brands(text: str) -> dict[str, Any]:
    """Find named brands/designers in free text and offer real substitutes.

    Returns {found: [...], message}. A brand is only reported if it does not
    directly match a catalog item name.
    """
    if not text:
        return {"found": [], "message": ""}
    low = text.lower()
    found = [b for b in KNOWN_BRANDS if b in low]
    if not found:
        return {"found": [], "message": ""}

    # A named brand is a problem only if the catalog has no item literally named
    # after it. (e.g. an "Eames" style chair may genuinely exist.)
    real_matches: dict[str, str] = {}
    unresolved: list[str] = []
    for brand in found:
        rows = db.query(
            "SELECT item_id, name FROM catalog WHERE lower(name) LIKE ?",
            (f"%{brand}%",),
        )
        if rows:
            real_matches[brand] = f"{rows[0]['name']} ({rows[0]['item_id']})"
        else:
            unresolved.append(brand)

    parts = []
    if unresolved:
        parts.append(
            "These named pieces are not in our catalog and I won't invent them: "
            + ", ".join(b.title() for b in unresolved)
            + ". I'll suggest the closest real alternatives by style instead."
        )
    if real_matches:
        parts.append(
            "Genuine matches found: "
            + "; ".join(f"{k.title()} -> {v}" for k, v in real_matches.items())
            + "."
        )
    return {"found": found, "unresolved": unresolved,
            "real_matches": real_matches, "message": " ".join(parts)}


# --- 3. Final-plan validators (pure code) --------------------------------

def all_items_real(item_ids: list[str], catalog_ids: set[str] | None = None) -> bool:
    if catalog_ids is None:
        catalog_ids = db.get_catalog_ids()
    return all(str(i) in catalog_ids for i in item_ids)


def hallucinated_items(item_ids: list[str], catalog_ids: set[str] | None = None) -> list[str]:
    if catalog_ids is None:
        catalog_ids = db.get_catalog_ids()
    return [str(i) for i in item_ids if str(i) not in catalog_ids]


def not_over_budget(total: float, budget: float | None) -> bool:
    if budget is None:
        return True
    return total <= float(budget)


GUARANTEE_PATTERNS = [
    r"guaranteed by", r"guaranteed delivery", r"price locked", r"locked price",
    r"delivered on \w+", r"will arrive on", r"guaranteed to arrive",
]


def no_guaranteed_language(text: str) -> bool:
    """True (pass) if the text avoids guaranteed-date / locked-price wording."""
    if not text:
        return True
    low = text.lower()
    return not any(re.search(p, low) for p in GUARANTEE_PATTERNS)
