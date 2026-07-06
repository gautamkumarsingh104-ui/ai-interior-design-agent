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


# --- 4. Dimension conflict detection -------------------------------------

_DIM_CM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*cm\s*[x×]\s*(\d+(?:\.\d+)?)\s*cm"
    r"(?:\s*[x×]\s*(\d+(?:\.\d+)?)\s*cm)?",
    re.IGNORECASE,
)
_DIM_BARE_RE = re.compile(
    r"(?:room|fit|space|area|size)[^\d]{0,30}(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def extract_dimensions_from_text(text: str) -> list[tuple[float, float, float | None]]:
    """Pull L×W×H (or L×W) size mentions from free text, in cm."""
    if not text:
        return []
    found: list[tuple[float, float, float | None]] = []
    for m in _DIM_CM_RE.finditer(text):
        l, w = float(m.group(1)), float(m.group(2))
        h = float(m.group(3)) if m.group(3) else None
        found.append((l, w, h))
    for m in _DIM_BARE_RE.finditer(text):
        l, w = float(m.group(1)), float(m.group(2))
        found.append((l, w, None))
    return found


def _dims_conflict(a: float, b: float, ratio_threshold: float = 3.0) -> bool:
    if a <= 0 or b <= 0:
        return False
    ratio = max(a, b) / min(a, b)
    return ratio >= ratio_threshold


def detect_dimension_conflict(
    length_cm: float | None,
    width_cm: float | None,
    free_text: str = "",
    constraints: str = "",
) -> dict[str, Any]:
    """Flag when free-text dimensions disagree with structured room fields."""
    text = " ".join(filter(None, [free_text, constraints])).strip()
    mentions = extract_dimensions_from_text(text)
    if not length_cm or not width_cm or not mentions:
        return {"conflict": False, "severe": False, "message": ""}

    conflicts: list[str] = []
    severe = False
    for tl, tw, th in mentions:
        len_conflict = _dims_conflict(float(length_cm), tl)
        wid_conflict = _dims_conflict(float(width_cm), tw)
        if not len_conflict and not wid_conflict:
            continue
        severe = severe or max(length_cm, width_cm) / min(tl, tw) >= 5
        parts = []
        if len_conflict:
            parts.append(f"length {int(tl)}cm in your note vs {int(length_cm)}cm entered")
        if wid_conflict:
            parts.append(f"width {int(tw)}cm in your note vs {int(width_cm)}cm entered")
        if th is not None:
            parts.append(f"height {int(th)}cm mentioned in note")
        conflicts.append(", ".join(parts))

    if not conflicts:
        return {"conflict": False, "severe": False, "message": ""}

    msg = (
        "Your note mentions different room dimensions than the form: "
        + "; ".join(conflicts)
        + ". Please confirm which is correct before we finalize a plan "
        "(e.g. did you mean metres instead of centimetres?)."
    )
    return {"conflict": True, "severe": severe, "message": msg,
            "structured": (length_cm, width_cm), "text_mentions": mentions}


# --- 5. Named catalog piece resolution -----------------------------------

# Generic furniture labels — never treat as a named-piece pin request.
_GENERIC_PIECES = {
    "sofa", "couch", "coffee table", "tv unit", "tv", "rug", "lighting", "light",
    "lamp", "armchair", "chair", "bookshelf", "side table", "rug", "carpet",
    "floor lamp", "table lamp", "wall art", "planter", "mirror", "curtains",
}


def _is_named_piece_request(chunk: str) -> bool:
    cl = chunk.lower().strip()
    for prefix in ("an ", "a ", "the "):
        if cl.startswith(prefix):
            cl = cl[len(prefix):].strip()
    if not cl or cl in _GENERIC_PIECES:
        return False
    return any(b in cl for b in KNOWN_BRANDS)


def resolve_catalog_pins(
    must_haves: list[str] | None = None,
    free_text: str = "",
    constraints: str = "",
) -> list[dict[str, Any]]:
    """Find catalog items the customer named explicitly (e.g. Eames lounger -> ACH-001)."""
    must_chunks: list[str] = []
    if must_haves:
        if isinstance(must_haves, list):
            must_chunks = [str(x).strip() for x in must_haves if x]
        else:
            must_chunks = [p.strip() for p in str(must_haves).split(",") if p.strip()]
    text = " ".join(filter(None, [free_text, constraints, ", ".join(must_chunks)])).lower()
    if not text and not must_chunks:
        return []

    pins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_pin(phrase: str, row: dict) -> None:
        iid = str(row["item_id"])
        if iid in seen_ids:
            return
        seen_ids.add(iid)
        pins.append({
            "phrase": phrase,
            "item_id": iid,
            "name": row["name"],
            "category": row["category"],
            "item": row,
        })

    # Named must-haves only (e.g. "an Eames lounger") — not generic "Sofa".
    for chunk in must_chunks:
        cl = chunk.lower().strip()
        if not _is_named_piece_request(chunk):
            continue
        for row in db.query(
            "SELECT * FROM catalog WHERE room_types LIKE '%Living Room%'"
        ):
            name = (row.get("name") or "").lower()
            if "eames" in cl and "eames" in name:
                _add_pin(chunk, row)

    extra_phrases = sorted(
        {p.lower().strip() for p in must_chunks if _is_named_piece_request(p)},
        key=len, reverse=True,
    )
    search_terms = extra_phrases + [
        "eames lounger", "eames lounge", "eames chair", "eames-style",
    ]

    for phrase in search_terms:
        if phrase not in text and not any(phrase in c.lower() for c in must_chunks):
            continue
        rows = db.query(
            "SELECT * FROM catalog WHERE lower(name) LIKE ? AND room_types LIKE ?",
            (f"%{phrase}%", "%Living Room%"),
        )
        for row in rows:
            _add_pin(phrase, row)

    return pins


# --- 6. Fit severity (code-level safety net) -------------------------------

SEVERE_FIT_FOOTPRINT_PCT = 100.0


def classify_fit_severity(fit_result: dict[str, Any]) -> str:
    """ok | tight | failed | severe — severe means physically impossible set."""
    if fit_result.get("fits"):
        pct = fit_result.get("footprint_used_pct")
        if pct is not None and pct > 85:
            return "tight"
        return "ok"
    pct = fit_result.get("footprint_used_pct")
    if pct is not None and pct > SEVERE_FIT_FOOTPRINT_PCT:
        return "severe"
    return "failed"
