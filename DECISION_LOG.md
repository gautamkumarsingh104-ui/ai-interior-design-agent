# Decision Log — AI Interior Design Agent (Living Room MVP)

## 1. Scope: in / out

**In scope — Living Room only.**
- The dataset gives 8 of 14 briefs as Living Room (BR-01, 02, 05, 06, 07, 08, 09, 14),
  so all guardrail traps (impossible budget, out-of-scope wall question, fake
  designer names, room-too-small) already live inside real Living Room cases —
  no invented scenarios needed.
- Living Room has the deepest catalog variety (sofas, coffee tables, TV units,
  rugs, lamps), giving richer search/substitution options.
- With a 6-8 hour window, one room fully correct (every guardrail solid) is
  stronger evidence of product judgment than five rooms handled shallowly.

**Out of scope (by design):**
- Bedroom, Dining, Study, Kids rooms.
- 3D / CAD layout — only an area-based heuristic fit-check.
- Accounts, login, multi-user, saved history.
- Guaranteed delivery dates or final negotiated pricing.
- Structural, electrical, plumbing advice (actively refused).
- Production-grade UI — a clean functional single page is enough.

## 2. How AI tools were directed — and where overridden

- **Directed:** Gemini is used for what code is bad at — parsing free-text briefs
  into structured intent, orchestrating the tool-calling plan loop, writing the
  style rationale, confirming out-of-scope intent, and acting as the eval
  style-coherence judge.
- **Overridden by code (the important part):** every hard constraint is enforced
  deterministically *after* the model proposes. `agent.run()` filters out any
  item_id not in the catalog, repairs budget by swapping to cheaper items (then
  dropping least-essential pieces), repairs fit by swapping to smaller items,
  and re-validates. The model can suggest; `guardrails.py` and the repair loop
  decide. Rationale text is checked for guaranteed-date / locked-price wording
  and falls back to a safe template if it fails.
- **Why:** an LLM alone will occasionally hallucinate an item, quietly bust the
  budget, or promise a delivery date. Those are exactly the failures this product
  cannot ship with, so they are guaranteed in code, not left to prompting.

## 3. Key design choices

- **LLM proposes, deterministic code disposes.** Satisfies the PRD's "multi-step
  agentic, tool-calling loop" requirement while guaranteeing correctness.
- **Fit-check is an area heuristic** (sum of major-piece footprints vs ~55% of
  floor area), explicitly not a spatial solver — sufficient for a sanity check.
- **Graceful degradation:** if no API key is present, the app still runs on the
  deterministic planner and template rationale; only LLM parsing/judging is lost.
- **Category vs room_type** are always filtered independently, per the schema note.
- **Live category matching:** must-have phrases are matched against the DB's real
  category names (plus a synonym map), so the app adapts to the actual catalog.

## 4. What would break in production

- **Fit-check is naive** — it ignores door/window placement, walkways, and piece
  shape. Real layouts need a spatial engine.
- **Style matching is substring-based** on `style_tags`; a proper taxonomy /
  embeddings would be more robust for fuzzy taste matching.
- **Single-currency, single-region**; no tax, shipping, or availability by pincode.
- **Brand detection uses a hard-coded list** of well-known designers; it will
  miss long-tail brand names. A retrieval/NER approach would generalize.
- **No caching / rate-limit handling** for the LLM; heavy traffic would need it.
- **No concurrency guarantees** beyond a read-only SQLite connection.

## 5. What's next

- Extend to the other four room types (the tools/guardrails are room-agnostic).
- Replace the area heuristic with a real 2D layout check (door/window aware).
- Add "explain this swap" transparency and let users pin/lock chosen items.
- Add embeddings-based style and substitute matching.
- Persist plans and support iterative refinement ("make it cheaper", "warmer").
- Wire real inventory + pricing APIs instead of a static snapshot.
