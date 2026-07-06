"""Read-only access layer for the interior_company_catalog.db SQLite file.

The database is GIVEN and must never be modified. Every connection is opened
in read-only mode (``mode=ro``) so a stray write can never corrupt it.
"""
from __future__ import annotations

import glob as _glob
import os
import sqlite3
from typing import Any

DB_FILENAME = "interior_company_catalog.db"

# Windows often hides extensions, so the given file may be .db / .sqlite / etc.
# We resolve it robustly rather than assuming one exact name.
_DB_STEM = "interior_company_catalog"
_DB_EXTS = (".db", ".sqlite", ".sqlite3", ".db3", ".s3db", "")


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def db_path() -> str:
    """Absolute path to the catalog DB in the project root.

    Preference order:
      1. Exactly interior_company_catalog.db
      2. interior_company_catalog.<any sqlite extension>
      3. Any single *.db / *.sqlite file in the root
    Falls back to the canonical name (so error messages are clear) if none found.
    """
    root = _project_root()
    canonical = os.path.join(root, DB_FILENAME)
    if os.path.exists(canonical):
        return canonical

    # Same stem, any known extension.
    for ext in _DB_EXTS:
        candidate = os.path.join(root, _DB_STEM + ext)
        if os.path.isfile(candidate):
            return candidate

    # Any sqlite-looking file in the root as a last resort.
    for pattern in ("*.db", "*.sqlite", "*.sqlite3", "*.db3", "*.s3db"):
        matches = [m for m in _glob.glob(os.path.join(root, pattern))
                   if os.path.isfile(m)]
        if matches:
            return matches[0]

    return canonical


def db_exists() -> bool:
    return os.path.isfile(db_path())


def get_connection() -> sqlite3.Connection:
    """Open a read-only connection. Raises a friendly error if the file is missing."""
    path = os.path.abspath(db_path())
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Could not find '{DB_FILENAME}'. Place the provided database file in "
            f"the project root: {os.path.dirname(path)}"
        )
    # Read-only URI connection so we can never accidentally mutate the given data.
    # Try multiple URI forms — Streamlit Cloud (Linux) can reject the plain form.
    uri_forms = [
        f"file:{path}?mode=ro",
        f"file:///{path.replace(os.sep, '/')}?mode=ro",
    ]
    last_err: Exception | None = None
    for uri in uri_forms:
        try:
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as exc:
            last_err = exc
    raise sqlite3.OperationalError(
        f"Could not open read-only database at {path}: {last_err}"
    )


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as plain dicts."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# --- Schema helpers -------------------------------------------------------

def table_columns(table: str) -> list[str]:
    rows = query(f"PRAGMA table_info({table})")
    return [r["name"] for r in rows]


def verify_schema() -> dict[str, Any]:
    """Best-effort check that the expected tables/columns are present.

    Returns a report rather than raising, so the UI can show a helpful message
    instead of crashing on a slightly different DB.
    """
    report: dict[str, Any] = {"ok": True, "problems": []}
    try:
        catalog_cols = set(table_columns("catalog"))
        briefs_cols = set(table_columns("room_briefs"))
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "problems": [f"Could not read schema: {exc}"]}

    expected_catalog = {
        "item_id", "category", "name", "style_tags", "price_inr",
        "width_cm", "depth_cm", "height_cm", "color_finish",
        "in_stock", "lead_time_days", "room_types",
    }
    expected_briefs = {
        "brief_id", "room_type", "length_cm", "width_cm", "ceiling_cm",
        "budget_inr", "style_preference", "must_haves", "constraints",
        "customer_note",
    }
    missing_catalog = expected_catalog - catalog_cols
    missing_briefs = expected_briefs - briefs_cols
    if missing_catalog:
        report["ok"] = False
        report["problems"].append(f"catalog missing columns: {sorted(missing_catalog)}")
    if missing_briefs:
        report["ok"] = False
        report["problems"].append(f"room_briefs missing columns: {sorted(missing_briefs)}")
    return report


# --- Catalog helpers ------------------------------------------------------

def get_catalog_ids() -> set[str]:
    """Set of every real item_id. Used by the anti-hallucination guardrail."""
    return {str(r["item_id"]) for r in query("SELECT item_id FROM catalog")}


def get_item(item_id: str) -> dict[str, Any] | None:
    rows = query("SELECT * FROM catalog WHERE item_id = ?", (item_id,))
    return rows[0] if rows else None


def get_items(item_ids: list[str]) -> list[dict[str, Any]]:
    if not item_ids:
        return []
    placeholders = ",".join("?" for _ in item_ids)
    rows = query(
        f"SELECT * FROM catalog WHERE item_id IN ({placeholders})", tuple(item_ids)
    )
    by_id = {str(r["item_id"]): r for r in rows}
    # Preserve caller ordering.
    return [by_id[i] for i in item_ids if i in by_id]


def distinct_categories(room_type: str | None = "Living Room") -> list[str]:
    if room_type:
        rows = query(
            "SELECT DISTINCT category FROM catalog WHERE room_types LIKE ?",
            (f"%{room_type}%",),
        )
    else:
        rows = query("SELECT DISTINCT category FROM catalog")
    return sorted({r["category"] for r in rows if r["category"]})


# --- Room brief helpers ---------------------------------------------------

def get_room_briefs(room_type: str | None = None) -> list[dict[str, Any]]:
    if room_type:
        return query(
            "SELECT * FROM room_briefs WHERE room_type LIKE ?", (f"%{room_type}%",)
        )
    return query("SELECT * FROM room_briefs")


def get_living_room_briefs() -> list[dict[str, Any]]:
    return get_room_briefs("Living Room")


def get_brief(brief_id: str) -> dict[str, Any] | None:
    rows = query("SELECT * FROM room_briefs WHERE brief_id = ?", (brief_id,))
    return rows[0] if rows else None
