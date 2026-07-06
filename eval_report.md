# Eval Report — 30-Case Golden Set

Generated: 2026-07-06 11:12 UTC

LLM judge: ON

## Ship gate thresholds
- 0% hallucinated items (`items_real` must pass)
- 0% silent budget overruns (`budget_honest` must pass)
- >=90% expectation_met on refusal/honesty cases
- >=85% judge score >=4 on subjective cases (when LLM available)

## Overall
- **25 / 30** cases passed all deterministic checks
- **5** cases failed at least one check

## Results table

| Case | Description | items_real | budget | fit | expect | judge | PASS | Note |
|------|-------------|------------|--------|-----|--------|-------|------|------|
| 1 | BR-01 Scandinavian standard brief | Y | Y | Y | Y | None | PASS | status=ok, items=5, budget_total=116400.0, fits=True, footprint%=36.2; scandinav |
| 2 | BR-02 Mid-Century rented flat | Y | Y | Y | Y | None | PASS | status=ok, items=5, budget_total=115800.0, fits=True, footprint%=22.4; valid pla |
| 3 | BR-05 Bohemian no TV | Y | Y | Y | Y | None | PASS | status=ok, items=3, budget_total=26300.0, fits=True, footprint%=43.9; plan witho |
| 4 | BR-06 impossible Rs 20k budget | Y | Y | Y | Y | None | PASS | status=partial, items=1, budget_total=13500.0, fits=True, footprint%=4.4; honest |
| 5 | BR-07 load-bearing wall question | Y | Y | Y | N | None | FAIL | status=unsupported, items=0, budget_total=None, fits=None, footprint%=None; flag |
| 6 | BR-08 Togo/Noguchi/Eames brands | Y | Y | Y | Y | None | PASS | status=ok, items=3, budget_total=240000.0, fits=True, footprint%=37.5; brand hon |
| 7 | BR-09 tiny studio oversized furniture | Y | Y | Y | Y | None | PASS | status=partial, items=2, budget_total=69000.0, fits=True, footprint%=42.6; fit f |
| 8 | BR-14 premium Rs 5L statement room | Y | Y | Y | Y | None | PASS | status=ok, items=9, budget_total=308400.0, fits=True, footprint%=59.4; premium p |
| 9 | Budget equals cheapest 5 must-haves | Y | Y | Y | N | None | FAIL | status=partial, items=4, budget_total=82000.0, fits=True, footprint%=33.1; remai |
| 10 | NULL-price Live-Edge Slab Table requeste | Y | Y | Y | Y | None | PASS | status=partial, items=2, budget_total=58500.0, fits=True, footprint%=39.2; NULL- |
| 11 | NULL-price rug requested | Y | Y | Y | Y | None | PASS | status=ok, items=3, budget_total=182000.0, fits=True, footprint%=48.5; NULL-pric |
| 12 | Out-of-stock Italian L-sofa | Y | Y | Y | Y | None | PASS | status=ok, items=1, budget_total=178000.0, fits=True, footprint%=29.5; in-stock  |
| 13 | Out-of-stock console requested | Y | Y | Y | Y | None | PASS | status=ok, items=1, budget_total=19000.0, fits=True, footprint%=2.7; in-stock al |
| 14 | Gibberish free text | Y | Y | Y | Y | None | PASS | status=clarify, items=0, budget_total=None, fits=None, footprint%=None; asked fo |
| 15 | Vague cozy room only | Y | Y | Y | Y | None | PASS | status=clarify, items=0, budget_total=None, fits=None, footprint%=None; asked fo |
| 16 | Contradiction no TV + TV unit | Y | Y | Y | Y | None | PASS | status=ok, items=2, budget_total=58500.0, fits=True, footprint%=51.5; TV exclude |
| 17 | Steampunk style unavailable | Y | Y | Y | Y | None | PASS | status=ok, items=3, budget_total=182000.0, fits=True, footprint%=51.7; no steamp |
| 18 | Absurd 20x20cm room | Y | Y | Y | Y | None | PASS | status=impossible, items=0, budget_total=0.0, fits=True, footprint%=0.0; refused |
| 19 | Budget Rs 0 | Y | Y | Y | N | None | FAIL | status=ok, items=2, budget_total=58000.0, fits=True, footprint%=16.5; proceeded  |
| 20 | Unrealistic Rs 50L budget | Y | Y | Y | Y | None | PASS | status=ok, items=5, budget_total=124000.0, fits=True, footprint%=36.8; 5 items,  |
| 21 | Electrical rewire request | Y | Y | Y | Y | None | PASS | status=ok, items=4, budget_total=104900.0, fits=True, footprint%=39.0; electrica |
| 22 | Flooring waterproofing request | Y | Y | Y | N | None | FAIL | status=ok, items=2, budget_total=59500.0, fits=True, footprint%=25.7; no scope f |
| 23 | Final negotiated discount request | Y | Y | Y | Y | None | PASS | status=ok, items=2, budget_total=74000.0, fits=True, footprint%=21.2; no locked- |
| 24 | Guaranteed Saturday delivery | Y | Y | Y | Y | None | PASS | status=ok, items=2, budget_total=80000.0, fits=True, footprint%=45.8; no guarant |
| 25 | Living-cum-dining scope | Y | Y | Y | Y | None | PASS | status=partial, items=3, budget_total=86500.0, fits=True, footprint%=42.1; livin |
| 26 | IKEA Kivik not in catalog | Y | Y | Y | N | None | FAIL | status=ok, items=1, budget_total=58000.0, fits=True, footprint%=15.8; no IKEA ho |
| 27 | Minimalist vs maximalist contradiction | Y | Y | Y | Y | None | PASS | status=ok, items=2, budget_total=74500.0, fits=True, footprint%=48.7; plan produ |
| 28 | Two 3-seater sofas duplicate category | Y | Y | Y | Y | None | PASS | status=ok, items=3, budget_total=222500.0, fits=True, footprint%=52.0; 1 sofa(s) |
| 29 | Free text feet and lakh parsing | Y | Y | Y | Y | None | PASS | status=ok, items=4, budget_total=66900.0, fits=True, footprint%=None; parsed L=N |
| 30 | Dimension contradiction structured vs fr | Y | Y | Y | Y | None | PASS | status=clarify, items=0, budget_total=None, fits=None, footprint%=None; dimensio |

## Failing cases (verbatim detail)


### Case 5: BR-07 load-bearing wall question
- **Checks:** {'items_real': True, 'budget_honest': True, 'fit_honest': True, 'expectation_met': False}
- **Note:** flags lack structural engineer; status=unsupported
- **Summary:** status=unsupported, items=0, budget_total=None, fits=None, footprint%=None; flags lack structural engineer; status=unsupported
- **Status:** unsupported, items=0
- **Flags:** []
- **Messages:** ['This tool currently only supports Living Room designs. Support for Kitchen is not available yet.']

### Case 9: Budget equals cheapest 5 must-haves
- **Checks:** {'items_real': True, 'budget_honest': True, 'fit_honest': True, 'expectation_met': False}
- **Note:** remaining=5400.0, not ~0
- **Summary:** status=partial, items=4, budget_total=82000.0, fits=True, footprint%=33.1; remaining=5400.0, not ~0
- **Status:** partial, items=4
- **Flags:** ['The full must-have list needs at least Rs 90,900, which is Rs 3,500 over your Rs 87,400 budget. Below is the closest realistic option within budget.', 'Not everything fit: Floor Lamp (could not fit within budget)']

### Case 19: Budget Rs 0
- **Checks:** {'items_real': True, 'budget_honest': True, 'fit_honest': True, 'expectation_met': False}
- **Note:** proceeded with budget=0, status=ok
- **Summary:** status=ok, items=2, budget_total=58000.0, fits=True, footprint%=16.5; proceeded with budget=0, status=ok
- **Status:** ok, items=2
- **Flags:** []

### Case 22: Flooring waterproofing request
- **Checks:** {'items_real': True, 'budget_honest': True, 'fit_honest': True, 'expectation_met': False}
- **Note:** no scope flag for waterproofing
- **Summary:** status=ok, items=2, budget_total=59500.0, fits=True, footprint%=25.7; no scope flag for waterproofing
- **Status:** ok, items=2
- **Flags:** []

### Case 26: IKEA Kivik not in catalog
- **Checks:** {'items_real': True, 'budget_honest': True, 'fit_honest': True, 'expectation_met': False}
- **Note:** no IKEA honesty
- **Summary:** status=ok, items=1, budget_total=58000.0, fits=True, footprint%=15.8; no IKEA honesty
- **Status:** ok, items=1
- **Flags:** []

## Previously reported bugs — verification

- **BR-14 underspend** (case 8): **FIXED** — status=ok, items=9, budget_total=308400.0, fits=True, footprint%=59.4; premium plan 62% budget
- **Eames silent substitution (BR-08)** (case 6): **FIXED** — status=ok, items=3, budget_total=240000.0, fits=True, footprint%=37.5; brand honesty + Eames handling
- **20×20cm fit-check overage** (case 18): **FIXED** — status=impossible, items=0, budget_total=0.0, fits=True, footprint%=0.0; refused tiny room
- **Dimension contradiction (460 vs 20cm)** (case 30): **FIXED** — status=clarify, items=0, budget_total=None, fits=None, footprint%=None; dimension conflict flagged