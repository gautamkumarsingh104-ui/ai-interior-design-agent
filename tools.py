"""Backend tools the agent calls during planning.

Each tool appends a structured record to a ``trace`` list so we can (a) show a
real tool-call reasoning trace in the UI and (b) verify in the eval harness
that the agent actually used its tools instead of getting lucky.
"""
from __future__ import annotations

from typing import Any

import db

# Categories that meaningfully consume floor space. Small accessories (lamps,
# cushions, art) are ignored by the footprint heuristic.
FOOTPRINT_CATEGORIES = {
    "sofa", "coffee table", "tv unit", "rug", "armchair", "sideboard",
    "bookshelf", "console", "console table", "side table", "ottoman",
    "pouffe", "accent chair", "recliner", "dining table",
}

# Fraction of the room floor that furniture may occupy; the rest is circulation.
CIRCULATION_FACTOR = 0.55


def _trace(trace: list | None, tool: str, args: dict, result: Any) -> None:
    if trace is not None:
        summary = result
        if isinstance(result, list):
            summary = f"{len(result)} rows"
        trace.append({"tool": tool, "args": args, "result": summary})


def _style_score(style_tags: str | None, wanted: list[str]) -> int:
    if not style_tags or not wanted:
        return 0
    tags = style_tags.lower()
    return sum(1 for w in wanted if w and w.lower() in tags)


def catalog_search(
    category: str,
    style_tags: list[str] | str | None = None,
    room_type: str = "Living Room",
    max_price: float | None = None,
    in_stock_only: bool = False,
    trace: list | None = None,
) -> list[dict[str, Any]]:
    """Query the catalog for items matching a category (+ style / budget / stock).

    Always filters on BOTH room_types AND category, per the schema note that the
    two columns are independent. NULL-price items are excluded by default because
    they cannot be reasoned about for budget.
    """
    if isinstance(style_tags, str):
        wanted = [t.strip() for t in style_tags.split(",") if t.strip()]
    else:
        wanted = list(style_tags or [])

    sql = (
        "SELECT * FROM catalog "
        "WHERE room_types LIKE ? AND lower(category) = lower(?) "
        "AND price_inr IS NOT NULL"
    )
    params: list[Any] = [f"%{room_type}%", category]
    if max_price is not None:
        sql += " AND price_inr <= ?"
        params.append(max_price)
    if in_stock_only:
        sql += " AND in_stock = 1"

    rows = db.query(sql, tuple(params))

    # Rank: best style match first, then in-stock, then cheapest.
    rows.sort(
        key=lambda r: (
            -_style_score(r.get("style_tags"), wanted),
            0 if r.get("in_stock") == 1 else 1,
            r.get("price_inr") if r.get("price_inr") is not None else float("inf"),
        )
    )
    _trace(
        trace,
        "catalog_search",
        {
            "category": category,
            "style_tags": wanted,
            "room_type": room_type,
            "max_price": max_price,
            "in_stock_only": in_stock_only,
        },
        rows,
    )
    return rows


def budget_calculator(
    selected_items: list[str],
    budget_inr: float,
    trace: list | None = None,
) -> dict[str, Any]:
    """Sum prices of selected items (skipping NULLs) and compare against budget."""
    items = db.get_items(selected_items)
    total = 0.0
    null_price_items: list[str] = []
    for it in items:
        price = it.get("price_inr")
        if price is None:
            null_price_items.append(str(it["item_id"]))
            continue
        total += float(price)
    result = {
        "total": round(total, 2),
        "budget": float(budget_inr) if budget_inr is not None else None,
        "remaining": round(float(budget_inr) - total, 2) if budget_inr is not None else None,
        "over_budget": (budget_inr is not None and total > float(budget_inr)),
        "null_price_items": null_price_items,
    }
    _trace(trace, "budget_calculator", {"selected_items": selected_items,
                                        "budget_inr": budget_inr}, result)
    return result


def fit_check(
    selected_items: list[str],
    room_length_cm: float | None,
    room_width_cm: float | None,
    trace: list | None = None,
    circulation_factor: float | None = None,
) -> dict[str, Any]:
    """Heuristic footprint check: do the major pieces fit with circulation space?

    Not a CAD/spatial solver -- an area-based sanity check. Items missing
    dimensions are skipped and reported so the caller can flag them.
    """
    items = db.get_items(selected_items)

    if not room_length_cm or not room_width_cm:
        result = {
            "fits": True,
            "footprint_used_pct": None,
            "skipped": [],
            "note": "Room dimensions unknown; fit-check skipped.",
        }
        _trace(trace, "fit_check", {"selected_items": selected_items,
                                    "room_length_cm": room_length_cm,
                                    "room_width_cm": room_width_cm}, result)
        return result

    room_area = float(room_length_cm) * float(room_width_cm)
    factor = circulation_factor if circulation_factor is not None else CIRCULATION_FACTOR
    usable_area = room_area * factor
    footprint = 0.0
    skipped: list[str] = []
    for it in items:
        cat = (it.get("category") or "").lower()
        if cat not in FOOTPRINT_CATEGORIES:
            continue  # small accessory, ignore for footprint
        w, d = it.get("width_cm"), it.get("depth_cm")
        if w is None or d is None:
            skipped.append(str(it["item_id"]))
            continue
        footprint += float(w) * float(d)

    fits = footprint <= usable_area
    result = {
        "fits": fits,
        "footprint_cm2": round(footprint, 1),
        "usable_area_cm2": round(usable_area, 1),
        "room_area_cm2": round(room_area, 1),
        "footprint_used_pct": round(footprint / room_area * 100, 1) if room_area else None,
        "skipped": skipped,
    }
    _trace(trace, "fit_check", {"selected_items": selected_items,
                                "room_length_cm": room_length_cm,
                                "room_width_cm": room_width_cm}, result)
    return result
