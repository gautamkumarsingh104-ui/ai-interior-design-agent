"""Streamlit single-page UI for the AI Interior Design Agent (Living Room MVP)."""
from __future__ import annotations

import html
import os

import streamlit as st

# Must be the first Streamlit command (Cloud shows a blank page if this runs late).
st.set_page_config(page_title="AI Interior Design Agent", page_icon="🛋️", layout="centered")

# Streamlit Cloud secrets → env vars (keys stay out of git).
try:
    for _key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL"):
        if _key in st.secrets and not os.environ.get(_key):
            os.environ[_key] = str(st.secrets[_key])
except Exception:
    pass

import db
import llm

STYLE_OPTIONS = [
    "Scandinavian", "Mid-Century", "Industrial", "Contemporary",
    "Bohemian", "Minimalist", "Coastal", "Traditional",
]

# Only Living Room is supported; others are shown but marked "Coming soon".
ROOM_TYPE_OPTIONS = [
    "Living Room",
    "Bedroom (Coming soon)",
    "Dining (Coming soon)",
    "Study (Coming soon)",
    "Kids (Coming soon)",
]

MUST_HAVE_OPTIONS = [
    "Sofa", "Coffee Table", "TV Unit", "Rug", "Lighting",
    "Armchair", "Bookshelf", "Side Table",
]

ACCENT = "#B5613C"

# --- Styling --------------------------------------------------------------

st.markdown(
    """
    <style>
      .block-container { max-width: 900px; padding-top: 2.2rem; }
      .hero-title { font-size: 2rem; font-weight: 700; margin-bottom: .1rem; }
      .hero-sub { color: #7a6f63; margin-bottom: 1.4rem; }
      .section-gap { margin-top: 1.8rem; }

      .plan-header { font-size: 1.4rem; font-weight: 700; margin: .2rem 0 1rem; }

      .item-card {
        background: #ffffff; border: 1px solid #e8ded0; border-radius: 14px;
        padding: 14px 18px; margin-bottom: 12px;
        display: flex; justify-content: space-between; align-items: center;
        box-shadow: 0 1px 2px rgba(60,45,30,.04);
      }
      .item-left { display: flex; flex-direction: column; gap: 4px; }
      .item-cat { font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
                  color: #a08a72; font-weight: 600; }
      .item-name { font-size: 1.05rem; font-weight: 600; color: #2b2724; }
      .item-meta { margin-top: 2px; }
      .chip { display: inline-block; background: #F1E7DA; color: #8a5a37;
              border-radius: 999px; padding: 2px 10px; font-size: .72rem;
              font-weight: 600; margin-right: 6px; }
      .item-price { font-size: 1.1rem; font-weight: 700; color: #2b2724;
                    white-space: nowrap; padding-left: 14px; }
      .item-warn { color: #b5401c; font-size: .76rem; margin-top: 4px; }

      .progress-track { background: #EAE0D2; border-radius: 999px; height: 16px;
                        width: 100%; overflow: hidden; }
      .progress-fill { height: 100%; border-radius: 999px; }
      .budget-caption { margin-top: 8px; color: #5c5348; font-size: .95rem; }
      .budget-caption b { color: #2b2724; }

      .badge { display: inline-flex; align-items: center; gap: 6px;
               border-radius: 999px; padding: 6px 14px; font-weight: 600;
               font-size: .9rem; }
      .badge-ok { background: #E4F1E4; color: #1f7a32; }
      .badge-warn { background: #FBE7E2; color: #B5401C; }

      .rationale-box { background: #F4EFE7; border-left: 4px solid #B5613C;
                       padding: 14px 18px; border-radius: 8px; color: #3a342d;
                       line-height: 1.5; }
      .tradeoff-list { margin: .2rem 0 0 0; padding-left: 0; list-style: none; }
      .tradeoff-list li { padding: 4px 0 4px 26px; position: relative; color: #4a4239; }
      .tradeoff-list li:before { content: "•"; color: #B5613C; position: absolute;
                                 left: 8px; font-weight: 700; }
      .label { font-size: .8rem; text-transform: uppercase; letter-spacing: .06em;
               color: #a08a72; font-weight: 700; margin: 1.4rem 0 .5rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛋️ AI Interior Design Agent")
st.markdown(
    '<div class="hero-sub">Real catalog items only · budget-aware · room-fit checked '
    '· honest about trade-offs</div>',
    unsafe_allow_html=True,
)


def _inr(value) -> str:
    if value is None:
        return "n/a"
    try:
        return f"₹{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


# --- Preflight checks -----------------------------------------------------

try:
    if not db.db_exists():
        st.error(
            f"Database file `{db.DB_FILENAME}` not found. Place the provided file in "
            f"the project root and reload."
        )
        st.stop()

    schema = db.verify_schema()
    if not schema["ok"]:
        st.warning("The database schema looks different from expected:")
        for p in schema["problems"]:
            st.write("- ", p)

    if not llm.is_available():
        st.info(
            "Gemini API key not detected — running on the deterministic fallback "
            "(rule-based planning and rationale). Add `GEMINI_API_KEY` to Streamlit "
            "Secrets or a local `.env` file for full LLM features."
        )

    briefs = db.get_living_room_briefs()
except Exception as exc:
    st.error("The app failed to start. Check Streamlit Cloud logs for details.")
    st.exception(exc)
    st.stop()
brief_by_id = {b["brief_id"]: b for b in briefs}

with st.sidebar:
    st.header("Quick test (optional)")
    st.caption("Shortcut for demos — loads one of the 8 real Living Room briefs into "
               "the form. The main flow is entering your own details.")
    options = ["(none)"] + [
        f"{b['brief_id']} — {(b.get('style_preference') or 'n/a')}" for b in briefs
    ]
    picked = st.selectbox("Load a sample brief", options, index=0)
    picked_id = picked.split(" — ")[0] if picked != "(none)" else None

sample = brief_by_id.get(picked_id) if picked_id else None


def _sv(key, default=""):
    return (sample.get(key) if sample else None) or default


def _default_must_haves() -> tuple[list[str], str]:
    """Split the sample's must-haves into (known checkboxes, other-text)."""
    raw = _sv("must_haves", "")
    if not raw:
        return ["Sofa", "Coffee Table", "TV Unit", "Rug", "Lighting"], ""
    tokens = [t.strip() for t in str(raw).split(",") if t.strip()]
    checked, other = [], []
    for tok in tokens:
        match = next((o for o in MUST_HAVE_OPTIONS if o.lower() in tok.lower()
                      or tok.lower() in o.lower()), None)
        if match and match not in checked:
            checked.append(match)
        elif not match:
            other.append(tok)
    return checked or ["Sofa"], ", ".join(other)


# --- Input form (PRIMARY input method) ------------------------------------

st.markdown('<div class="label">Design your room</div>', unsafe_allow_html=True)
default_checks, default_other = _default_must_haves()

with st.form("brief_form"):
    room_type = st.selectbox(
        "Room type", ROOM_TYPE_OPTIONS, index=0,
        help="This MVP supports Living Room only. Other rooms are coming soon.",
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        length_cm = st.number_input("Length (cm)", min_value=0,
                                    value=int(_sv("length_cm", 0) or 0))
    with c2:
        width_cm = st.number_input("Width (cm)", min_value=0,
                                   value=int(_sv("width_cm", 0) or 0))
    with c3:
        ceiling_cm = st.number_input("Ceiling (cm)", min_value=0,
                                     value=int(_sv("ceiling_cm", 0) or 0))

    c4, c5 = st.columns(2)
    with c4:
        budget_inr = st.number_input("Budget (₹)", min_value=0, step=5000,
                                     value=int(_sv("budget_inr", 0) or 0))
    with c5:
        style_default = _sv("style_preference", "Scandinavian")
        style_index = STYLE_OPTIONS.index(style_default) if style_default in STYLE_OPTIONS else 0
        style_preference = st.selectbox("Style", STYLE_OPTIONS, index=style_index)

    must_have_checks = st.multiselect(
        "Must-haves", MUST_HAVE_OPTIONS, default=default_checks,
    )
    other_must_haves = st.text_input(
        "Other must-haves (comma separated, optional)", value=default_other,
    )
    constraints = st.text_area("Constraints (optional)", value=_sv("constraints", ""),
                               height=70, placeholder="e.g. rented flat, no fixed installations")
    free_text = st.text_area(
        "Anything else we should know? (optional)",
        value=_sv("customer_note", ""), height=90,
        placeholder="e.g. named brands, unusual requests, questions...",
    )

    submitted = st.form_submit_button("Generate My Plan", type="primary",
                                      use_container_width=True)


# --- Rendering helpers ----------------------------------------------------

def _render_budget_bar(plan: dict) -> None:
    br = plan.get("budget_result", {})
    total = br.get("total") or 0
    budget = plan.get("budget")
    st.markdown('<div class="label">Budget</div>', unsafe_allow_html=True)
    if not budget:
        st.markdown(
            f'<div class="budget-caption"><b>{_inr(total)}</b> total '
            f'(no budget set)</div>', unsafe_allow_html=True)
        return
    pct = total / float(budget) * 100 if budget else 0
    over = br.get("over_budget")
    fill_color = "#B5401C" if over else ACCENT
    width = min(pct, 100)
    st.markdown(
        f'<div class="progress-track">'
        f'<div class="progress-fill" style="width:{width:.0f}%;background:{fill_color}">'
        f'</div></div>'
        f'<div class="budget-caption"><b>{_inr(total)}</b> used of '
        f'<b>{_inr(budget)}</b> &nbsp;·&nbsp; {pct:.0f}% &nbsp;·&nbsp; '
        f'{_inr(br.get("remaining"))} remaining</div>',
        unsafe_allow_html=True,
    )
    if over:
        st.markdown(
            '<div class="item-warn">This selection exceeds the budget.</div>',
            unsafe_allow_html=True)


def _render_fit_badge(plan: dict) -> None:
    fit = plan.get("fit_result", {})
    pct = fit.get("footprint_used_pct")
    if pct is None:
        return
    st.markdown('<div class="label">Room fit</div>', unsafe_allow_html=True)
    if fit.get("fits"):
        st.markdown(
            f'<span class="badge badge-ok">✓ Fits — footprint uses ~{pct:.0f}% '
            f'of the floor</span>', unsafe_allow_html=True)
    else:
        st.markdown(
            f'<span class="badge badge-warn">⚠ Tight fit — footprint ~{pct:.0f}% '
            f'of the floor</span>', unsafe_allow_html=True)


def _render_items(plan: dict) -> None:
    items = plan.get("items", [])
    if not items:
        return
    st.markdown('<div class="label">Your plan</div>', unsafe_allow_html=True)
    style = plan.get("style")
    for it in items:
        chip = html.escape(str(style)) if style else ""
        chip_html = f'<span class="chip">{chip}</span>' if chip else ""
        warns = it.get("flags", [])
        warn_html = ""
        if warns:
            warn_html = '<div class="item-warn">⚠ ' + \
                html.escape("; ".join(warns)) + '</div>'
        st.markdown(
            f'<div class="item-card"><div class="item-left">'
            f'<span class="item-cat">{html.escape(str(it["category"]))}</span>'
            f'<span class="item-name">{html.escape(str(it["name"]))}</span>'
            f'<span class="item-meta">{chip_html}'
            f'<span style="color:#a08a72;font-size:.78rem;">'
            f'{html.escape(str(it["item_id"]))}</span></span>'
            f'{warn_html}'
            f'</div>'
            f'<div class="item-price">{_inr(it.get("price"))}</div></div>',
            unsafe_allow_html=True,
        )


def _render_rationale(plan: dict) -> None:
    if plan.get("rationale"):
        st.markdown('<div class="label">Why these picks</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="rationale-box">{html.escape(plan["rationale"])}</div>',
            unsafe_allow_html=True)
    trade = plan.get("trade_offs")
    if trade:
        st.markdown('<div class="label">Left out / trade-offs</div>',
                    unsafe_allow_html=True)
        # Split into bullet points on sentence/`;` boundaries for a list feel.
        parts = [p.strip() for p in trade.replace("; ", ". ").split(". ") if p.strip()]
        lis = "".join(f"<li>{html.escape(p.rstrip('.'))}.</li>" for p in parts)
        st.markdown(f'<ul class="tradeoff-list">{lis}</ul>', unsafe_allow_html=True)


def _render_flags(plan: dict) -> None:
    flags = plan.get("flags", [])
    if not flags:
        return
    st.markdown('<div class="label">Notes & flags</div>', unsafe_allow_html=True)
    for f in flags:
        st.warning(f)


def _render_trace(plan: dict) -> None:
    trace = plan.get("tool_trace", [])
    with st.expander(f"🔧 Tool-call reasoning trace ({len(trace)} calls)"):
        if not trace:
            st.write("No tool calls recorded.")
        for i, t in enumerate(trace, 1):
            st.write(f"{i}. `{t['tool']}` — args: {t['args']}")
            st.caption(f"→ {t['result']}")


def render_plan(plan: dict, title_suffix: str = "") -> None:
    status = plan["status"]
    version = plan.get("version", 1)
    label = f" (v{version})" if version > 1 or title_suffix else title_suffix

    if status == "unsupported":
        st.warning(plan["messages"][0])
        return

    if status == "clarify":
        st.warning(plan["messages"][0] if plan.get("messages")
                   else "Please clarify your brief.")
        _render_flags(plan)
        _render_trace(plan)
        return

    header = f"{plan['room_type']} Plan — {plan.get('style') or 'Custom'} Style{label}"
    if version > 1:
        st.markdown(f'<div class="plan-header">Revised Plan{label} — '
                    f'{html.escape(plan.get("style") or "Custom")} Style</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="plan-header">{html.escape(header)}</div>',
                    unsafe_allow_html=True)

    if status == "impossible":
        st.error("A full plan isn't possible from the catalog for this brief. See notes below.")
    elif status == "partial":
        st.warning("Here is the closest realistic option — not everything could be included.")

    _render_items(plan)
    _render_budget_bar(plan)
    _render_fit_badge(plan)
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
    _render_rationale(plan)
    _render_flags(plan)
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
    _render_trace(plan)


# --- Run the agent --------------------------------------------------------

if "brief_input" not in st.session_state:
    st.session_state.brief_input = None
if "plan_v1" not in st.session_state:
    st.session_state.plan_v1 = None
if "plan_v2" not in st.session_state:
    st.session_state.plan_v2 = None
if "refined" not in st.session_state:
    st.session_state.refined = False


def _build_brief_input():
    must_haves_combined = ", ".join(
        [m for m in must_have_checks]
        + ([other_must_haves] if other_must_haves.strip() else [])
    )
    return {
        "room_type": room_type.replace(" (Coming soon)", ""),
        "length_cm": length_cm or None,
        "width_cm": width_cm or None,
        "ceiling_cm": ceiling_cm or None,
        "budget_inr": budget_inr or None,
        "style_preference": style_preference,
        "must_haves": must_haves_combined,
        "constraints": constraints,
        "free_text": free_text,
    }


if submitted:
    st.session_state.brief_input = _build_brief_input()
    st.session_state.refined = False
    st.session_state.plan_v2 = None
    with st.spinner("Designing your room..."):
        try:
            import agent
            st.session_state.plan_v1 = agent.run(st.session_state.brief_input)
        except Exception as exc:
            st.exception(exc)
            st.session_state.plan_v1 = None

if st.session_state.plan_v1:
    render_plan(st.session_state.plan_v1)

    # One refinement round: v1 -> v2 (same brief + feedback, same guardrails).
    if not st.session_state.refined:
        st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
        st.markdown('<div class="label">Want changes?</div>', unsafe_allow_html=True)
        with st.form("refine_form"):
            refine_text = st.text_area(
                "Not happy with this plan? Tell us what to change",
                placeholder='e.g. "add wall art and a second light source, use a better sofa"',
                height=80,
            )
            refine_clicked = st.form_submit_button("Refine Plan", type="secondary")
        if refine_clicked and refine_text.strip():
            with st.spinner("Refining your plan..."):
                try:
                    import agent
                    st.session_state.plan_v2 = agent.run(
                        st.session_state.brief_input, feedback=refine_text.strip()
                    )
                    st.session_state.refined = True
                except Exception as exc:
                    st.exception(exc)

    if st.session_state.plan_v2:
        st.markdown("---")
        render_plan(st.session_state.plan_v2)

elif not submitted:
    st.info("Enter your room details above and click **Generate My Plan**. "
            "(Or use the optional sample loader in the sidebar for a quick demo.)")
