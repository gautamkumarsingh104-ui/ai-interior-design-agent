"""Multi-step interior design agent (Living Room only).

Architecture (see PRD section 10):
  1. Parse the brief into structured intent (Gemini for free text).
  2. Run guardrail pre-checks (out-of-scope, named brands).
  3. LLM tool-calling orchestrator proposes a plan using catalog_search /
     budget_calculator / fit_check.
  4. Deterministic guardrail gate validates the proposal and REPAIRS it
     (swap-on-fail for budget and fit); code has the final veto.
  5. Generate rationale + explicit trade-offs.

The LLM proposes; deterministic code guarantees every hard constraint.
"""
from __future__ import annotations

import re
from typing import Any

import db
import guardrails
import llm
import tools

MAX_LLM_TOOL_ITERS = 5
MAX_REPAIR_ROUNDS = 4

DEFAULT_MUST_HAVES = ["sofa", "coffee table", "tv unit", "rug", "lighting"]

SUPPORTED_ROOM_TYPE = "Living Room"

# Room types this MVP does not support yet. Detected before any tool call so we
# never fabricate a plan for an unsupported room.
UNSUPPORTED_ROOMS = {
    "bedroom": "Bedroom", "master bedroom": "Bedroom", "guest room": "Bedroom",
    "dining": "Dining", "dining room": "Dining",
    "study": "Study", "home office": "Study", "office": "Study",
    "kids": "Kids", "kid": "Kids", "children": "Kids", "child": "Kids",
    "nursery": "Kids", "playroom": "Kids",
    "kitchen": "Kitchen", "bathroom": "Bathroom", "balcony": "Balcony",
}


def detect_unsupported_room(brief_input: dict, free_text: str) -> str | None:
    """Return an unsupported room label if the user asked for a non-Living-Room.

    Checks the explicit room-type field first, then free text (but only when the
    user did NOT also mention a living room, to avoid false positives).
    """
    rt = (brief_input.get("room_type") or "").strip().lower()
    if rt:
        if "living" in rt:
            return None
        for key, label in UNSUPPORTED_ROOMS.items():
            if key in rt:
                return label
    ft = (free_text or "").lower()
    if ft and "living" not in ft:
        for key, label in UNSUPPORTED_ROOMS.items():
            if key in ft:
                return label
    return None

# Maps a keyword found in a must-have phrase to candidate tokens we look for in
# the catalog's real category names (which we don't hard-code, we match live).
CATEGORY_SYNONYMS = {
    "sofa": ["sofa", "couch"],
    "couch": ["sofa", "couch"],
    "settee": ["sofa"],
    "sectional": ["sofa", "sectional"],
    "coffee table": ["coffee"],
    "centre table": ["coffee"],
    "tv unit": ["tv", "media", "entertainment", "lowboard"],
    "tv": ["tv", "media", "entertainment"],
    "media": ["tv", "media"],
    "lowboard": ["tv", "lowboard", "media"],
    "rug": ["rug", "carpet"],
    "carpet": ["rug", "carpet"],
    "lighting": ["lamp", "light"],
    "light": ["lamp", "light"],
    "lamp": ["lamp", "light"],
    "floor lamp": ["floor lamp", "lamp"],
    "armchair": ["armchair", "accent chair"],
    "accent chair": ["accent chair", "armchair"],
    "chair": ["chair", "armchair"],
    "bookshelf": ["bookshelf", "shelf", "book"],
    "shelf": ["shelf", "book"],
    "sideboard": ["sideboard", "credenza"],
    "console": ["console"],
    "side table": ["side table", "end table"],
    "curtain": ["curtain", "drape"],
    "cushion": ["cushion", "pillow"],
    "pouffe": ["pouffe", "ottoman", "pouf"],
    "ottoman": ["ottoman", "pouffe"],
    "wall art": ["wall art", "art", "artwork", "painting", "print", "framed"],
    "art": ["art", "artwork", "painting", "print", "wall decor"],
    "artwork": ["art", "artwork", "painting"],
    "painting": ["art", "artwork", "painting"],
    "table lamp": ["table lamp", "desk lamp"],
    "floor lamp": ["floor lamp", "standing lamp"],
    "pendant": ["pendant", "ceiling light", "hanging light", "chandelier"],
    "planter": ["planter", "plant", "pot"],
    "mirror": ["mirror"],
    "seating for 4": ["sofa", "couch", "sectional"],
    "seating": ["sofa", "couch", "sectional"],
    "reading corner": ["armchair", "floor lamp", "side table"],
    "layered rugs": ["rug", "carpet"],
    "layered rug": ["rug", "carpet"],
    "plants": ["planter", "plant"],
    "plant": ["planter", "plant"],
    "accent seating": ["armchair", "accent chair"],
    "l-sectional": ["sofa", "sectional"],
    "l sectional": ["sofa", "sectional"],
    "lounger": ["armchair", "accent chair", "recliner"],
    "eames lounger": ["armchair", "accent chair"],
    "eames": ["armchair", "accent chair"],
    "designer sofa": ["sofa"],
    "3-seater": ["sofa"],
    "3 seater": ["sofa"],
    "full living room": ["sofa", "coffee table", "tv unit", "rug", "lighting"],
}

# The "premium living room" superset used when a customer signals a generous /
# "impress us" budget and their brief is otherwise sparse.
PREMIUM_MUST_HAVE_PHRASES = [
    "sofa", "coffee table", "tv unit", "rug", "wall art",
    "floor lamp", "table lamp", "armchair",
]

# Language that signals the customer wants a complete, high-end plan (not cheapest).
GENEROUS_PHRASES = [
    "impress", "comfortable", "no real constraint", "no constraint", "no constraints",
    "high-end", "high end", "premium", "statement", "splurge", "luxury", "luxurious",
    "go all out", "spare no expense", "best you", "top of the line", "upscale",
    "designer", "layered lighting",
]

# Words in refinement feedback that mean "make it better / add more".
UPGRADE_WORDS = [
    "better", "upgrade", "premium", "nicer", "more", "add", "statement",
    "quality", "improve", "high-end", "luxur", "bigger", "second", "another",
]

# Lower index = more essential; used when we must drop pieces to fit budget/room.
_PRIORITY_ORDER = [
    "sofa", "coffee", "tv", "media", "rug", "armchair", "accent chair",
    "sideboard", "console", "bookshelf", "side table", "lamp", "light",
    "curtain", "cushion", "pouffe", "ottoman", "art",
]


def category_priority(category: str) -> int:
    c = (category or "").lower()
    for i, tok in enumerate(_PRIORITY_ORDER):
        if tok in c:
            return i
    return len(_PRIORITY_ORDER)


# --- Brief parsing --------------------------------------------------------

def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = [str(v).strip() for v in value if str(v).strip()]
    else:
        raw = [p.strip() for p in str(value).split(",") if p.strip()]
    out: list[str] = []
    for p in raw:
        if ":" in p:
            before, after = p.split(":", 1)
            if before.strip():
                out.append(before.strip())
            if after.strip():
                out.append(after.strip())
        else:
            out.append(p)
    return out


def parse_brief(brief_input: dict[str, Any]) -> dict[str, Any]:
    """Merge structured fields with LLM-parsed free text into one intent dict."""
    free_text = (brief_input.get("free_text") or brief_input.get("customer_note")
                 or "").strip()

    intent: dict[str, Any] = {
        "room_type": "Living Room",
        "length_cm": _num(brief_input.get("length_cm")),
        "width_cm": _num(brief_input.get("width_cm")),
        "ceiling_cm": _num(brief_input.get("ceiling_cm")),
        "budget_inr": _num(brief_input.get("budget_inr")),
        "style_preference": (brief_input.get("style_preference") or "").strip(),
        "must_haves": _as_list(brief_input.get("must_haves")),
        "constraints": (brief_input.get("constraints") or "").strip(),
        "free_text": free_text,
    }

    # If the structured form is thin but there's free text, let the LLM fill gaps.
    thin = (not intent["must_haves"] and intent["budget_inr"] is None
            and intent["length_cm"] is None and not intent["style_preference"])
    if free_text and thin and llm.is_available():
        parsed = llm.generate_json(
            "Extract an interior-design brief from the text below into JSON with keys: "
            "length_cm (int cm or null), width_cm (int cm or null), "
            "ceiling_cm (int cm or null), budget_inr (int rupees or null), "
            "style_preference (string or null), "
            "must_haves (array of furniture pieces), constraints (string or null). "
            "If the text is gibberish or has no design intent, return "
            '{"gibberish": true}.\n\n'
            f"Text: {free_text}"
        )
        if isinstance(parsed, dict):
            if parsed.get("gibberish"):
                intent["gibberish"] = True
            else:
                intent["length_cm"] = intent["length_cm"] or _num(parsed.get("length_cm"))
                intent["width_cm"] = intent["width_cm"] or _num(parsed.get("width_cm"))
                intent["ceiling_cm"] = intent["ceiling_cm"] or _num(parsed.get("ceiling_cm"))
                intent["budget_inr"] = intent["budget_inr"] or _num(parsed.get("budget_inr"))
                intent["style_preference"] = (intent["style_preference"]
                                              or (parsed.get("style_preference") or "").strip())
                intent["must_haves"] = intent["must_haves"] or _as_list(parsed.get("must_haves"))
                if not intent["constraints"]:
                    intent["constraints"] = (parsed.get("constraints") or "").strip()
    return intent


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


# --- Category resolution --------------------------------------------------

def resolve_categories(must_haves: list[str],
                       available: list[str]) -> tuple[list[str], list[str], set[str]]:
    """Map must-have phrases to real catalog categories.

    Returns (resolved_categories, unresolved_phrases, excluded_categories).
    Negations like "no TV" are treated as exclusions.
    """
    exclusions: set[str] = set()
    resolved: list[str] = []
    unresolved: list[str] = []

    for phrase in must_haves:
        p = phrase.lower().strip()
        negated = p.startswith(("no ", "without ", "not ", "skip "))
        cat = _match_category(p, available)
        if cat is None:
            if not negated:
                unresolved.append(phrase)
            continue
        if negated:
            exclusions.add(cat)
        elif cat not in resolved:
            resolved.append(cat)

    # Honour "no TV" beating "TV unit" -> drop excluded categories.
    resolved = [c for c in resolved if c not in exclusions]
    return resolved, unresolved, exclusions


def extract_categories_from_text(text: str, available: list[str]) -> list[str]:
    """Find catalog categories mentioned anywhere in free-form text (e.g. feedback)."""
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    # Direct catalog category names.
    for cat in available:
        if cat.lower() in low and cat not in found:
            found.append(cat)
    # Synonym keywords.
    for key in CATEGORY_SYNONYMS:
        if _keyword_in_text(key, low):
            cat = _match_category(key, available)
            if cat and cat not in found:
                found.append(cat)
    return found


# Phrases that describe intent/mood, not a catalog category — never show as "left out".
INTENT_ONLY_PHRASES = [
    "premium statement", "statement living", "living room", "high-end", "high end",
    "impress us", "impress me", "no real constraint", "no constraint", "no constraints",
    "comfortable budget", "look high-end", "designer look", "open plan", "open up",
    "lots of texture", "source these specific", "designer pieces i want",
    "just source", "make it all fit", "open up the space", "living-dining",
    "matchy-matchy", "moved in and want", "keep it cosy", "keep it cozy",
]


def is_intent_phrase(phrase: str) -> bool:
    p = phrase.lower().strip()
    if not p or len(p.split()) >= 6:
        return True  # long free-text blobs are intent, not a product category
    return any(k in p for k in INTENT_ONLY_PHRASES)


def _keyword_in_text(key: str, text: str) -> bool:
    """Match product keywords without false hits (e.g. 'art' inside 'budget')."""
    if len(key) <= 4:
        return bool(re.search(r"\b" + re.escape(key) + r"\b", text))
    return key in text


def expand_must_haves_from_brief(intent: dict) -> list[str]:
    """Use structured must-haves only; pull product keywords (not whole sentences) from notes."""
    parts: list[str] = list(intent.get("must_haves") or [])
    structured_text = ", ".join(parts).lower()
    for field in ("free_text", "constraints"):
        text = (intent.get(field) or "").strip()
        if not text:
            continue
        low = text.lower()
        for key in sorted(CATEGORY_SYNONYMS, key=len, reverse=True):
            if _keyword_in_text(key, low) and key not in structured_text:
                parts.append(key)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        key = p.lower().strip()
        if key and key not in seen and not is_intent_phrase(p):
            seen.add(key)
            out.append(p.strip())
    return out


def expand_layered_lighting(must_haves: list[str], resolved: list[str],
                            available: list[str], excluded: set[str]) -> None:
    """'Layered lighting' means more than one light source — add all lamp types."""
    text = " ".join(must_haves).lower()
    if "layered light" not in text and "layered lighting" not in text:
        return
    for cat in lighting_categories(available):
        if cat not in resolved and cat not in excluded:
            resolved.append(cat)


def expand_reading_corner(must_haves: list[str], resolved: list[str],
                          available: list[str], excluded: set[str]) -> None:
    """A reading corner needs seating + light (+ optional side table)."""
    if "reading corner" not in " ".join(must_haves).lower():
        return
    for phrase in ("armchair", "floor lamp", "side table"):
        cat = _match_category(phrase, available)
        if cat and cat not in resolved and cat not in excluded:
            resolved.append(cat)


def premium_categories(available: list[str]) -> list[str]:
    """The premium living-room superset, mapped to categories the catalog offers."""
    cats, _, _ = resolve_categories(PREMIUM_MUST_HAVE_PHRASES, available)
    return cats


def lighting_categories(available: list[str]) -> list[str]:
    """All lighting-type categories in the catalog (for 'layered lighting')."""
    out = []
    for cat in available:
        c = cat.lower()
        if any(tok in c for tok in ("lamp", "light", "pendant", "sconce", "chandelier")):
            out.append(cat)
    return out


def is_generous_selection(intent: dict, base_min_cost: float | None,
                          n_categories: int) -> bool:
    """Customer wants higher-quality picks (not necessarily more categories)."""
    text = " ".join(filter(None, [
        intent.get("free_text"), intent.get("constraints"),
        ", ".join(intent.get("must_haves") or []),
    ])).lower()
    if any(p in text for p in GENEROUS_PHRASES):
        return True
    budget = intent.get("budget_inr")
    # Budget-only signal: very comfortable vs a lean complete set AND few explicit asks.
    if budget and base_min_cost and base_min_cost > 0 and n_categories <= 4:
        if budget >= 3 * base_min_cost:
            return True
    return False


def should_expand_premium_set(intent: dict, n_categories: int) -> bool:
    """Add the premium living-room superset only for sparse, high-end briefs."""
    structured = ", ".join(intent.get("must_haves") or []).lower()
    # Customer named specific designer pieces — honour only those, do not pad the plan.
    if any(n in structured for n in ("togo", "noguchi", "eames", "specific piece")):
        return False
    text = " ".join(filter(None, [
        intent.get("free_text"), intent.get("constraints"), structured,
    ])).lower()
    # Explicit "fill out a premium living room" signals — always expand.
    if any(p in text for p in (
        "statement living", "premium statement", "impress us", "impress me",
        "designer sofa", "high-end", "high end", "comfortable; impress",
    )):
        return True
    return False


def is_generous(intent: dict, base_min_cost: float | None) -> bool:
    """Back-compat wrapper."""
    n = len(intent.get("must_haves") or [])
    return is_generous_selection(intent, base_min_cost, n)


def select_items(candidates: dict[str, list[dict]], style: str,
                 generous: bool, intent: dict | None = None) -> dict[str, dict]:
    """Pick one item per category.

    - Normal briefs: cheapest best-style in-stock match (candidates are pre-sorted).
    - Generous briefs: highest-quality (highest-price) item among the best style
      matches, preferring in-stock; honours 'impress us' / designer intent.
    """
    wanted = [style] if style else []
    text = " ".join(filter(None, [
        (intent or {}).get("free_text"), (intent or {}).get("constraints"),
        ", ".join((intent or {}).get("must_haves") or []),
    ])).lower()
    designer_ask = any(w in text for w in ("designer", "premium", "statement", "impress"))
    selection: dict[str, dict] = {}
    for cat, rows in candidates.items():
        if not rows:
            continue
        if not generous:
            selection[cat] = rows[0]
            continue
        best_score = max(tools._style_score(r.get("style_tags"), wanted) for r in rows)
        pool = [r for r in rows
                if tools._style_score(r.get("style_tags"), wanted) == best_score]
        if not pool:
            pool = list(rows)
        # Prefer in-stock; include OOS premium only when no in-stock premium exists.
        in_stock = [r for r in pool if r.get("in_stock") == 1]
        if in_stock:
            pool = in_stock
        elif designer_ask:
            pool = pool  # allow OOS premium (flagged later) if nothing in stock
        else:
            pool = pool

        def _rank(r: dict) -> tuple:
            name = (r.get("name") or "").lower()
            premium_boost = 1 if any(k in name for k in ("premium", "designer", "maison", "italian", "sectional", "l-sofa", "l sofa")) else 0
            pure_style = 1 if style and style.lower() in (r.get("style_tags") or "").lower().split(",") else 0
            price = _price(r) if _price(r) != float("inf") else -1
            return (premium_boost, pure_style, price)

        selection[cat] = max(pool, key=_rank)
    return selection


def _match_category(phrase: str, available: list[str]) -> str | None:
    # Direct substring match against real category names.
    for cat in available:
        cl = cat.lower()
        if cl in phrase or phrase in cl:
            return cat
    # Synonym-token match — longest key first (e.g. "accent seating" before "seating").
    for key in sorted(CATEGORY_SYNONYMS, key=len, reverse=True):
        if key not in phrase:
            continue
        tokens = CATEGORY_SYNONYMS[key]
        for cat in available:
            if any(tok in cat.lower() for tok in tokens):
                return cat
    return None


# --- Deterministic selection & repair ------------------------------------

def gather_candidates(categories: list[str], style: str, budget: float | None,
                      trace: list) -> dict[str, list[dict]]:
    style_tags = [style] if style else None
    candidates: dict[str, list[dict]] = {}
    for cat in categories:
        rows = tools.catalog_search(
            category=cat, style_tags=style_tags, room_type="Living Room", trace=trace
        )
        candidates[cat] = rows
    return candidates


def greedy_select(candidates: dict[str, list[dict]]) -> dict[str, dict]:
    """Pick the top-ranked candidate per category (best style, in-stock, cheapest)."""
    selection: dict[str, dict] = {}
    for cat, rows in candidates.items():
        if rows:
            selection[cat] = rows[0]
    return selection


def _price(item: dict) -> float:
    p = item.get("price_inr")
    return float(p) if p is not None else float("inf")


def _footprint(item: dict) -> float:
    w, d = item.get("width_cm"), item.get("depth_cm")
    if w is None or d is None:
        return 0.0
    return float(w) * float(d)


def repair_budget(selection: dict[str, dict], candidates: dict[str, list[dict]],
                  budget: float | None, trace: list, dropped: list) -> bool:
    """Swap/drop until within budget. Returns True if it changed the selection."""
    if budget is None:
        return False
    changed = False
    for _ in range(50):
        ids = [it["item_id"] for it in selection.values()]
        result = tools.budget_calculator(ids, budget, trace=trace)
        if not result["over_budget"]:
            return changed
        # Try swapping the most expensive selected item for a cheaper candidate.
        swapped = False
        for cat in sorted(selection, key=lambda c: _price(selection[c]), reverse=True):
            current_price = _price(selection[cat])
            for cand in candidates.get(cat, []):
                if _price(cand) < current_price:
                    selection[cat] = cand
                    swapped = True
                    break
            if swapped:
                break
        if swapped:
            changed = True
            continue
        # Nothing left to swap: drop the least-essential piece.
        if _drop_least_essential(selection, dropped, "budget"):
            changed = True
            continue
        return changed
    return changed


def repair_fit(selection: dict[str, dict], candidates: dict[str, list[dict]],
               room_l: float | None, room_w: float | None, trace: list,
               dropped: list, generous: bool = False, style: str = "") -> bool:
    """Swap/drop until the room fits. Returns True if it changed the selection."""
    if not room_l or not room_w:
        return False
    factor = _circulation_factor(room_l, room_w, generous)
    changed = False
    for _ in range(50):
        ids = [it["item_id"] for it in selection.values()]
        result = tools.fit_check(ids, room_l, room_w, trace=trace,
                                 circulation_factor=factor)
        if result["fits"]:
            return changed
        # Swap the largest-footprint piece for a smaller alternative.
        swapped = False
        for cat in sorted(selection, key=lambda c: _footprint(selection[c]), reverse=True):
            current_fp = _footprint(selection[cat])
            smaller = [c for c in candidates.get(cat, [])
                       if 0 < _footprint(c) < current_fp]
            if not smaller:
                continue
            if generous:
                wanted = [style] if style else []
                cur_style = tools._style_score(selection[cat].get("style_tags"), wanted)
                styled = [c for c in smaller
                          if not wanted or tools._style_score(c.get("style_tags"), wanted) >= cur_style]
                pool = styled if styled else smaller
                selection[cat] = max(pool, key=_price)
            else:
                selection[cat] = smaller[0]  # cheapest smaller (list is price-sorted)
            swapped = True
            break
        if swapped:
            changed = True
            continue
        if _drop_least_essential(selection, dropped, "fit", by_footprint=True):
            changed = True
            continue
        return changed
    return changed


def _circulation_factor(room_l: float | None, room_w: float | None,
                        generous: bool) -> float:
    """Large premium rooms get a slightly relaxed footprint allowance."""
    if not room_l or not room_w:
        return tools.CIRCULATION_FACTOR
    area = float(room_l) * float(room_w)
    if generous and area >= 180000:  # e.g. 450x400cm or 520x380cm
        return 0.62
    return tools.CIRCULATION_FACTOR


def _plan_fits(selection: dict[str, dict], room_l: float | None, room_w: float | None,
               trace: list, generous: bool = False) -> bool:
    if not room_l or not room_w:
        return True
    ids = [it["item_id"] for it in selection.values()]
    factor = _circulation_factor(room_l, room_w, generous)
    return tools.fit_check(ids, room_l, room_w, trace=trace,
                           circulation_factor=factor).get("fits", True)


def _plan_total(selection: dict[str, dict], budget: float | None, trace: list) -> float:
    if not selection:
        return 0.0
    ids = [it["item_id"] for it in selection.values()]
    return tools.budget_calculator(ids, budget or 0, trace=trace).get("total", 0.0)


def upgrade_generous_plan(selection: dict[str, dict], candidates: dict[str, list[dict]],
                          budget: float | None, room_l: float | None, room_w: float | None,
                          trace: list, intent: dict | None = None,
                          target_pct: float = 0.45, generous: bool = True) -> bool:
    """For generous briefs: upgrade to pricier in-stock items while budget & fit allow.

    Fixes high-budget underspend (e.g. BR-14 leaving 80%+ unused).
    """
    if not budget or not selection:
        return False
    target = float(budget) * target_pct
    changed_any = False
    for _ in range(30):
        total = _plan_total(selection, budget, trace)
        if total >= target:
            break
        upgraded = False
        for cat in sorted(selection.keys()):
            current = selection[cat]
            cur_price = _price(current)
            style = (intent or {}).get("style_preference", "") if intent else ""
            wanted = [style] if style else []
            cur_style = tools._style_score(current.get("style_tags"), wanted)
            for cand in reversed(candidates.get(cat, [])):
                cand_price = _price(cand)
                if cand_price <= cur_price or cand.get("in_stock") != 1:
                    continue
                if wanted and tools._style_score(cand.get("style_tags"), wanted) < cur_style:
                    continue
                old = selection[cat]
                selection[cat] = cand
                new_total = _plan_total(selection, budget, trace)
                fits = _plan_fits(selection, room_l, room_w, trace, generous=generous)
                if new_total <= budget and fits:
                    upgraded = True
                    changed_any = True
                    break
                selection[cat] = old  # revert
            if upgraded:
                break
        if not upgraded:
            break
    return changed_any


def build_closest_partial_plan(selection: dict[str, dict], candidates: dict[str, list[dict]],
                               resolved: list[str], budget: float | None,
                               trace: list, dropped: list) -> None:
    """When the full list cannot fit the budget, fill with the best affordable subset."""
    if selection or not budget:
        return
    spent = 0.0
    for cat in sorted(resolved, key=category_priority):
        rows = sorted(
            [r for r in candidates.get(cat, []) if r.get("price_inr") is not None],
            key=lambda r: float(r["price_inr"]),
        )
        for row in rows:
            price = float(row["price_inr"])
            if spent + price <= float(budget):
                selection[cat] = row
                spent += price
                break
        else:
            dropped.append({"category": cat, "item": None,
                            "reason": "could not fit within budget"})
    if selection:
        tools.budget_calculator([it["item_id"] for it in selection.values()],
                                budget, trace=trace)


def _drop_least_essential(selection: dict[str, dict], dropped: list, reason: str,
                          by_footprint: bool = False) -> bool:
    if not selection:
        return False
    if by_footprint:
        # Drop the biggest space-hog among the least essential pieces.
        cat = max(selection, key=lambda c: (category_priority(c), _footprint(selection[c])))
    else:
        cat = max(selection, key=lambda c: (category_priority(c), _price(selection[c])))
    item = selection.pop(cat)
    dropped.append({
        "category": cat,
        "item": item.get("name"),
        "reason": ("could not fit within budget" if reason == "budget"
                   else "could not fit in the room"),
    })
    return True


# --- LLM tool-calling orchestrator (the proposer) ------------------------

def _trim_row(r: dict) -> dict:
    return {
        "item_id": r.get("item_id"),
        "name": r.get("name"),
        "price_inr": r.get("price_inr"),
        "style_tags": r.get("style_tags"),
        "in_stock": r.get("in_stock"),
        "width_cm": r.get("width_cm"),
        "depth_cm": r.get("depth_cm"),
        "category": r.get("category"),
    }


def llm_orchestrate(intent: dict, categories: list[str], trace: list) -> list[str] | None:
    """Let Gemini drive the tool loop and propose item_ids. Returns None on any issue."""
    client = llm.get_client()
    if client is None:
        return None
    try:
        from google.genai import types
    except Exception:
        return None

    finalized: dict[str, Any] = {}

    def dispatch(name: str, args: dict) -> Any:
        if name == "catalog_search":
            rows = tools.catalog_search(
                category=args.get("category"),
                style_tags=args.get("style_tags"),
                room_type="Living Room",
                max_price=args.get("max_price"),
                in_stock_only=bool(args.get("in_stock_only", False)),
                trace=trace,
            )
            return {"items": [_trim_row(r) for r in rows[:12]]}
        if name == "budget_calculator":
            return tools.budget_calculator(
                args.get("selected_items", []), intent.get("budget_inr"), trace=trace
            )
        if name == "fit_check":
            return tools.fit_check(
                args.get("selected_items", []),
                intent.get("length_cm"), intent.get("width_cm"), trace=trace,
            )
        if name == "finalize_plan":
            finalized["item_ids"] = args.get("item_ids", [])
            return {"ok": True}
        return {"error": f"unknown tool {name}"}

    def _schema(props, required):
        return types.Schema(type=types.Type.OBJECT, properties=props, required=required)

    S = types.Schema
    T = types.Type
    decls = [
        types.FunctionDeclaration(
            name="catalog_search",
            description="Find Living Room catalog items in a category, ranked by style match, stock and price.",
            parameters=_schema({
                "category": S(type=T.STRING),
                "style_tags": S(type=T.ARRAY, items=S(type=T.STRING)),
                "max_price": S(type=T.NUMBER),
                "in_stock_only": S(type=T.BOOLEAN),
            }, ["category"]),
        ),
        types.FunctionDeclaration(
            name="budget_calculator",
            description="Sum prices of selected item_ids and compare to the budget.",
            parameters=_schema({
                "selected_items": S(type=T.ARRAY, items=S(type=T.STRING)),
            }, ["selected_items"]),
        ),
        types.FunctionDeclaration(
            name="fit_check",
            description="Check whether the selected item_ids' footprint fits the room.",
            parameters=_schema({
                "selected_items": S(type=T.ARRAY, items=S(type=T.STRING)),
            }, ["selected_items"]),
        ),
        types.FunctionDeclaration(
            name="finalize_plan",
            description="Submit the final chosen item_ids (one per must-have category).",
            parameters=_schema({
                "item_ids": S(type=T.ARRAY, items=S(type=T.STRING)),
            }, ["item_ids"]),
        ),
    ]

    system = (
        "You are an interior design planning agent for LIVING ROOMS only. "
        "Use the tools to build a plan: for EACH must-have category call catalog_search, "
        "pick ONE item per category, call budget_calculator to stay within budget, and "
        "call fit_check to ensure the pieces fit the room. Prefer in-stock, style-matching "
        "items. If over budget or the room is too small, search again and pick cheaper or "
        "smaller items. Only ever use item_ids returned by catalog_search - never invent one. "
        "When done, call finalize_plan with the chosen item_ids."
    )
    user = (
        f"Style: {intent.get('style_preference') or 'unspecified'}. "
        f"Budget (INR): {intent.get('budget_inr')}. "
        f"Room: {intent.get('length_cm')}cm x {intent.get('width_cm')}cm. "
        f"Must-have categories: {categories}."
    )

    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=decls)],
        system_instruction=system,
        temperature=0.2,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [types.Content(role="user", parts=[types.Part(text=user)])]

    try:
        for _ in range(MAX_LLM_TOOL_ITERS):
            resp = client.models.generate_content(
                model=llm.MODEL_NAME, contents=contents, config=config
            )
            if not resp.candidates:
                break
            parts = resp.candidates[0].content.parts or []
            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
            if not calls:
                break
            contents.append(resp.candidates[0].content)
            responses = []
            for fc in calls:
                result = dispatch(fc.name, dict(fc.args or {}))
                responses.append(types.Part.from_function_response(
                    name=fc.name, response={"result": result}))
            contents.append(types.Content(role="user", parts=responses))
            if "item_ids" in finalized:
                break
    except Exception:
        return None

    ids = finalized.get("item_ids")
    return [str(i) for i in ids] if ids else None


# --- Rationale & trade-offs ----------------------------------------------

def generate_rationale(intent: dict, items: list[dict], budget_result: dict,
                       left_out: list, flags: list) -> tuple[str, str]:
    """Return (rationale, trade_offs).

    The trade-off text is ALWAYS built deterministically from the actual
    left_out list, so it can never falsely claim completeness. Only the
    'why these picks' rationale is delegated to the LLM (with a safe fallback).
    """
    trade = _template_trade(intent, budget_result, left_out)
    style = intent.get("style_preference") or "the requested"
    if llm.is_available() and items:
        item_lines = "\n".join(
            f"- {it['category']}: {it['name']} (Rs {it.get('price') or 'n/a'})" for it in items
        )
        text = llm.generate_text(
            "Write ONLY a short design rationale (2-3 sentences) tying the chosen "
            "items to the customer's style and brief. Do NOT list trade-offs here. "
            "Do NOT promise delivery dates or locked prices; lead times are estimates.\n\n"
            f"Style: {style}. Budget: Rs {intent.get('budget_inr')}. "
            f"Remaining: Rs {budget_result.get('remaining')}.\n"
            f"Constraints: {intent.get('constraints')}.\n"
            f"Selected items:\n{item_lines}\n",
            temperature=0.5,
        )
        if text and guardrails.no_guaranteed_language(text):
            return text.strip(), trade
    rationale, _ = _template_rationale(intent, items, budget_result, left_out)
    return rationale, trade


def _template_trade(intent: dict, budget_result: dict, left_out: list) -> str:
    """Honest trade-off text, checked against the real left_out list."""
    parts = []
    remaining = budget_result.get("remaining")
    budget = intent.get("budget_inr")
    if remaining is not None:
        if budget:
            parts.append(f"Rs {int(remaining):,} of the Rs {int(budget):,} budget remains.")
        else:
            parts.append(f"Rs {int(remaining):,} remaining.")
    if left_out:
        d = "; ".join(
            f"{x.get('category')} ({x.get('reason')})" for x in left_out
        )
        parts.append(f"Left out: {d}.")
    else:
        parts.append("Every requested must-have category was filled.")
    return " ".join(parts)


def _template_rationale(intent: dict, items: list[dict], budget_result: dict,
                        left_out: list) -> tuple[str, str]:
    style = intent.get("style_preference") or "your"
    cats = ", ".join(it["category"] for it in items) or "no items"
    rationale = (
        f"This {style} living room plan covers {cats}, chosen to match your style "
        f"and make sensible use of your budget."
    )
    return rationale, _template_trade(intent, budget_result, left_out)


# --- Main entry point -----------------------------------------------------

def run(brief_input: dict[str, Any], feedback: str | None = None) -> dict[str, Any]:
    """Run the full agent pipeline and return a DesignPlan dict.

    If `feedback` is given (one refinement round), it is folded into the SAME
    brief as an extra constraint -- the original must-haves are preserved and
    the plan still goes through budget_calculator and fit_check.
    """
    trace: list = []
    flags: list[str] = []
    messages: list[str] = []

    force_generous = False
    if feedback and feedback.strip():
        fb = feedback.strip()
        brief_input = dict(brief_input)
        brief_input["free_text"] = (
            (brief_input.get("free_text") or "") + " | Refinement request: " + fb
        ).strip()
        # "make it better / add more" language forces premium selection.
        force_generous = any(w in fb.lower() for w in UPGRADE_WORDS)
        flags.append(f"Revised per your feedback: \"{fb}\"")

    intent = parse_brief(brief_input)

    # Guardrail: this MVP only supports Living Room. Detect any other requested
    # room type BEFORE any catalog/budget/fit tool call and stop honestly.
    unsupported = detect_unsupported_room(brief_input, intent.get("free_text", ""))
    if unsupported:
        return {
            "status": "unsupported",
            "room_type": unsupported,
            "style": intent.get("style_preference"),
            "budget": intent.get("budget_inr"),
            "items": [],
            "flags": [],
            "messages": [
                f"This tool currently only supports Living Room designs. "
                f"Support for {unsupported} is not available yet."
            ],
            "rationale": "",
            "trade_offs": "",
            "dropped": [],
            "tool_trace": trace,
            "intent": intent,
        }

    # Guardrail pre-checks over any free text / constraints / note.
    scope_text = " ".join(filter(None, [intent.get("free_text"), intent.get("constraints")]))
    scope = guardrails.classify_scope(scope_text)
    if scope["out_of_scope"]:
        flags.append(scope["message"])

    # Gibberish / empty brief -> ask for clarification instead of guessing.
    available = db.distinct_categories("Living Room")
    must_haves = expand_must_haves_from_brief(intent)
    intent["must_haves"] = must_haves
    if not must_haves and not intent.get("gibberish"):
        # No pieces named but a real brief exists -> use a sensible default set.
        if intent["budget_inr"] or intent["length_cm"] or intent["style_preference"]:
            must_haves = list(DEFAULT_MUST_HAVES)

    brand = guardrails.detect_named_brands(
        " ".join(filter(None, [intent.get("free_text", ""), ", ".join(must_haves)]))
    )
    if brand.get("message"):
        flags.append(brand["message"])

    resolved, unresolved, excluded = resolve_categories(must_haves, available)
    initial_category_count = len(resolved)
    expand_layered_lighting(must_haves, resolved, available, excluded)
    expand_reading_corner(must_haves, resolved, available, excluded)

    # Refinement feedback can add new categories (e.g. "add wall art"), but must
    # never drop the original must-haves.
    if feedback and feedback.strip():
        for cat in extract_categories_from_text(feedback, available):
            if cat not in resolved and cat not in excluded:
                resolved.append(cat)
        fb_low = feedback.lower()
        if any(w in fb_low for w in ("light", "lamp", "layer", "second", "another")):
            for cat in lighting_categories(available):
                if cat not in resolved and cat not in excluded:
                    resolved.append(cat)

    # Drop intent/mood phrases from unresolved — they are not product categories.
    unresolved = [p for p in unresolved if not is_intent_phrase(p)]

    if intent.get("gibberish") or (not resolved and not intent["budget_inr"]
                                   and not intent["length_cm"]):
        return {
            "status": "clarify",
            "room_type": "Living Room",
            "style": intent.get("style_preference"),
            "budget": intent.get("budget_inr"),
            "items": [],
            "flags": flags,
            "messages": [
                "I couldn't understand your brief. Please tell me your room size "
                "(length x width in cm), budget in rupees, preferred style, and the "
                "pieces you need (e.g. sofa, coffee table, TV unit, rug, lighting)."
            ],
            "rationale": "",
            "trade_offs": "",
            "dropped": [],
            "tool_trace": trace,
            "intent": intent,
        }

    if unresolved:
        flags.append(
            "No catalog match for: " + ", ".join(unresolved)
            + ". These were skipped (real items only)."
        )

    # Gather candidates once (shared by LLM proposer and deterministic repair).
    style = intent.get("style_preference", "")
    candidates = gather_candidates(resolved, style, intent.get("budget_inr"), trace)

    base_min = _min_full_cost(candidates, resolved)
    generous = force_generous or is_generous_selection(intent, base_min, len(resolved))
    expand_set = should_expand_premium_set(intent, initial_category_count)

    # Sparse premium briefs (e.g. BR-14): fill out a complete premium living room.
    if expand_set:
        for cat in premium_categories(available):
            if cat not in resolved and cat not in excluded:
                resolved.append(cat)
        missing_cats = [c for c in resolved if c not in candidates]
        if missing_cats:
            candidates.update(gather_candidates(missing_cats, style,
                                                intent.get("budget_inr"), trace))

    # Step 3: build the proposed plan.
    dropped: list = []
    selection: dict[str, dict] = {}
    if generous:
        selection = select_items(candidates, style, generous=True, intent=intent)
    else:
        proposed_ids = llm_orchestrate(intent, resolved, trace)
        if proposed_ids:
            catalog_ids = db.get_catalog_ids()
            proposed_ids = [i for i in proposed_ids if i in catalog_ids]
            for iid in proposed_ids:
                item = db.get_item(iid)
                if item and item.get("room_types") and "Living Room" in item["room_types"]:
                    cat = item["category"]
                    if cat in resolved and cat not in selection:
                        selection[cat] = item
        for cat in resolved:
            if cat not in selection and candidates.get(cat):
                selection[cat] = candidates[cat][0]

    # Categories with no catalog item at all -> record as unfulfillable.
    for cat in resolved:
        if cat not in selection:
            dropped.append({"category": cat, "item": None,
                            "reason": "no matching catalog item"})

    # Step 4: deterministic guardrail gate -> repair budget & fit (swap-on-fail).
    for _ in range(MAX_REPAIR_ROUNDS):
        changed = repair_budget(selection, candidates, intent.get("budget_inr"),
                                trace, dropped)
        changed = repair_fit(selection, candidates, intent.get("length_cm"),
                             intent.get("width_cm"), trace, dropped,
                             generous=generous,
                             style=intent.get("style_preference", "")) or changed
        if not changed:
            break

    # Step 4b: tight/impossible budgets — still return the closest affordable subset.
    if not selection and intent.get("budget_inr"):
        build_closest_partial_plan(selection, candidates, resolved,
                                   intent.get("budget_inr"), trace, dropped)

    # Step 5: generous briefs — upgrade tier while budget & fit allow (fixes underspend).
    if generous:
        text_low = " ".join(filter(None, [
            intent.get("free_text"), intent.get("constraints"),
        ])).lower()
        target = 0.50 if "impress" in text_low else 0.45
        upgrade_generous_plan(selection, candidates, intent.get("budget_inr"),
                              intent.get("length_cm"), intent.get("width_cm"), trace,
                              intent=intent, target_pct=target)

    final_ids = [it["item_id"] for it in selection.values()]
    circ = _circulation_factor(intent.get("length_cm"), intent.get("width_cm"), generous)
    budget_result = tools.budget_calculator(final_ids, intent.get("budget_inr"), trace=trace)
    fit_result = tools.fit_check(final_ids, intent.get("length_cm"),
                                 intent.get("width_cm"), trace=trace,
                                 circulation_factor=circ)

    # Per-item flags: out-of-stock, null price, null dimensions.
    items: list[dict] = []
    for cat, it in selection.items():
        item_flags: list[str] = []
        if it.get("in_stock") == 0:
            lt = it.get("lead_time_days")
            item_flags.append(
                f"Out of stock - estimated lead time ~{lt} days" if lt
                else "Out of stock - lead time to be confirmed"
            )
        if it.get("price_inr") is None:
            item_flags.append("Price unavailable - please confirm before purchase")
        if it.get("width_cm") is None or it.get("depth_cm") is None:
            item_flags.append("Dimensions not available - please confirm before purchase")
        items.append({
            "category": cat,
            "item_id": it.get("item_id"),
            "name": it.get("name"),
            "price": it.get("price_inr"),
            "in_stock": it.get("in_stock"),
            "lead_time_days": it.get("lead_time_days"),
            "width_cm": it.get("width_cm"),
            "depth_cm": it.get("depth_cm"),
            "height_cm": it.get("height_cm"),
            "style_tags": it.get("style_tags"),
            "color_finish": it.get("color_finish"),
            "flags": item_flags,
        })

    # Preserve must-have ordering in the output.
    order = {cat: i for i, cat in enumerate(resolved)}
    items.sort(key=lambda x: order.get(x["category"], 999))

    # Determine overall status.
    budget = intent.get("budget_inr")
    min_full_cost = _min_full_cost(candidates, resolved)
    shortfall_note = None
    if budget is not None and min_full_cost is not None and min_full_cost > budget:
        shortfall_note = (
            f"The full must-have list needs at least Rs {int(min_full_cost):,}, "
            f"which is Rs {int(min_full_cost - budget):,} over your Rs {int(budget):,} "
            f"budget. Below is the closest realistic option within budget."
        )
        flags.append(shortfall_note)

    if not items:
        status = "impossible"
    elif dropped or shortfall_note or not fit_result.get("fits", True):
        status = "partial"
    else:
        status = "ok"

    # Honesty: the "left out" list combines dropped categories AND requested
    # must-haves that had no catalog match, so trade-off text can never falsely
    # claim completeness.
    left_out = list(dropped)
    for phrase in unresolved:
        if not is_intent_phrase(phrase):
            left_out.append({"category": phrase, "item": None,
                             "reason": "no matching catalog item"})

    if left_out and status == "ok":
        status = "partial"

    if dropped:
        flags.append(
            "Not everything fit: " + "; ".join(
                f"{d['category']} ({d['reason']})" for d in dropped
            )
        )

    rationale, trade_offs = generate_rationale(intent, items, budget_result,
                                               left_out, flags)

    return {
        "status": status,
        "room_type": "Living Room",
        "style": intent.get("style_preference"),
        "budget": budget,
        "items": items,
        "budget_result": budget_result,
        "fit_result": fit_result,
        "rationale": rationale,
        "trade_offs": trade_offs,
        "flags": flags,
        "dropped": dropped,
        "messages": messages,
        "tool_trace": trace,
        "intent": intent,
        "version": 2 if feedback else 1,
    }


def _min_full_cost(candidates: dict[str, list[dict]], categories: list[str]) -> float | None:
    total = 0.0
    have_any = False
    for cat in categories:
        rows = candidates.get(cat, [])
        priced = [r for r in rows if r.get("price_inr") is not None]
        if not priced:
            continue
        have_any = True
        total += min(float(r["price_inr"]) for r in priced)
    return total if have_any else None
