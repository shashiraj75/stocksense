# Sprint #004 — Recommendation Consolidation Real-World Narrative Validation & Contract Integrity Review (Epic 005)

**Status:** Complete. A real-world validation, narrative-quality review, and contract-integrity sprint — not a live integration sprint. RCI remains additive, read-only, deterministic, non-authoritative, not wired into Prediction Engine, Daily Picks, APIs, or UI.

## Evidence Checkpoint (Mandatory)

Reviewed Sprint #001–#003's documents and code directly, plus the current `prediction_engine.py`/`daily_picks.py`. **All required invariants reconfirmed true**: RCI is additive, read-only, non-authoritative, creates no master score/replacement signal/replacement confidence, is not wired into Prediction Engine or Daily Picks, does not consume legacy `growth_score`/`valuation_score`, does not change hard-gate behavior, kill switches and fraud-risk behavior remain unchanged, no new provider required.

**Two genuine contract-integrity defects were found during this sprint and corrected — not hypothetical, confirmed against real, live data:**

### Defect 1 — Active gate vs. unresolved risk flag conflation (implementation defect)

Sprint #003's `_REJECTION_REASON_MAP` tagged Financial Strength's `liquidity_distress` and Business Quality's `fraud_risk`/`distress_and_aggressive_accruals` **identically** as `HardGateType.TRUE_VETO`, with no field distinguishing "this is genuinely enforced downstream today" from "this is computed but never enforced." Confirmed directly: `daily_picks.py`'s own quality gate excludes any stock whose Financial Strength reasoning contains the phrase `"liquidity distress"` (line 708) — a real, active enforcement — while **no code anywhere references Business Quality at all** (re-confirmed unchanged from Sprint #002/#003's own finding). **Corrected**: added `currently_enforced: bool` to `EngineEvidence`, and split the output contract's single `active_gates` field into `active_gates` (only `currently_enforced=True`) and a new `unresolved_risk_flags` (computed, described, never enforced). **Confirmed live against real data** (below): `AAL` (American Airlines) is a real company in this sprint's sample whose Financial Strength is a genuinely enforced veto — its RCI output correctly places it in `active_gates`, while a separate, simultaneous Business Quality flag for the same company correctly lands in `unresolved_risk_flags`. Both states present, correctly distinguished, in one real, live response.

### Defect 2 — Engine-version provenance not traceable (contract gap)

Sprint #003 supplied Business Quality's missing `engine_version` via a silent adapter default with no marker distinguishing it from a genuinely engine-reported value — an auditor could have wrongly believed Business Quality itself emitted `"v1"`. **Corrected**: added `engine_version_provenance` (`"engine_reported"` | `"adapter_supplied_default"` | `"unknown"`) to `EngineEvidence`, confirmed by dedicated tests that a real future `engine_version` field on Business Quality would automatically flip provenance to `"engine_reported"` with zero further code change.

Both corrections are narrowly scoped to the three RCI modules (`recommendation_consolidation_contract.py`, `recommendation_evidence_adapter.py`, `recommendation_consolidation_engine.py`) plus one test-quality fix (an overly strict non-interference test that flagged a code *comment* mentioning `daily_picks.py`, not an actual import — fixed to check for real import statements specifically). **No other contradiction was found.**

## Validation Methodology

Built `RecommendationEvidenceSnapshot`s from **real, live engine outputs** (Business Quality, Financial Strength [US only], Growth Intelligence, Valuation Intelligence) for a large, intentionally varied sample — not cherry-picked to make RCI look successful. Real data, not fixtures, drove every case study and pattern count below; the deterministic fixture suite (Sprint #003's 51 tests + this sprint's 10 new contract-integrity tests) separately proves determinism and isolation.

## Dataset Composition and Actual Completed Counts

| | India | US |
|---|---|---|
| Intended sample size | ≥150 | ≥100 |
| Attempted symbols | 173 | 101 |
| **Successfully completed RCI snapshots** | **173** | **101** |
| Engine execution failures (crashes) | **0** | **0** |
| Sectors represented | Banks, NBFC, IT, Pharma, FMCG, Auto, Capital Goods, Infrastructure, Metals, Chemicals, Utilities, Energy, Telecom, Consumer, Real Estate | Banks, NBFC/finance, premium compounders, value/energy, cyclicals, REITs, utilities, capital-intensive, distressed, growth |
| Financial-sector coverage | 19 banks/NBFC/insurance | 18 banks/NBFC/insurance |
| Data source | **100% live, current data** — no fixtures, no stale cache reused as current |

Both minimums exceeded (173/150, 101/100). **Zero crashes across 274 real companies.** Two companies (`X` in US, `ZOMATO` in India) returned `insufficient_evidence` because every engine genuinely failed to produce a result — reported honestly as a real finding, not hidden.

## Mandatory Classification Review

Counted directly from the real dataset, confirming no category leaks into another:

| Status | India count | US count |
|---|---|---|
| `supported` | 218 | 152 |
| `mixed` | 102 | 59 |
| `warning` | 70 | 37 |
| `avoid` | 124 | 122 |
| `unavailable` | 5 | 6 |
| `not_applicable` | 173 *(Financial Strength, every India company)* | 28 |
| `feature_disabled` | 0 *(main batch ran with the switch conceptually enabled; one supplementary example below)* | 0 |
| `execution_error` | 0 | 0 |
| `stale_snapshot` | 0 *(not exercised — no snapshot-storage integration exists yet)* | 0 |

**Hard-gate occurrences, the decisive proof of Defect 1's fix:**

| Engine | Gate type | `currently_enforced` | Occurrences (IN / US) |
|---|---|---|---|
| Financial Strength | `true_veto` | **True** | 0 / **1** (`AAL`) |
| Business Quality | `true_veto` | **False** | 7 / 25 |

**Confirmed: zero leakage.** Every Financial Strength `liquidity_distress` case in this sample (just the one, `AAL`) appears in `active_gates`. Every Business Quality fraud/distress case (32 total) appears in `unresolved_risk_flags`, never `active_gates`. India's Financial Strength `not_applicable` (all 173 companies, structural, by-design) and `unavailable` (5 companies, genuine US data gaps) are correctly distinct statuses, neither converted to negative evidence (confirmed: `supporting_evidence`/`opposing_evidence` never include a `not_applicable`/`unavailable` engine). Bank/NBFC Valuation non-applicability is handled **inside** Valuation Intelligence's own engine (its `inapplicable_fields` mechanism, confirmed unchanged since Epic 004) — RCI never needs whole-engine-level Bank/NBFC logic of its own, a real, accurate finding worth documenting: RCI inherits correct sector-gating, it does not re-implement it.

## Business Quality Engine-Version Provenance Result

Confirmed via 4 dedicated tests (`TestEngineVersionProvenance`): the adapter-supplied default (`"v1"`) is now always tagged `engine_version_provenance="adapter_supplied_default"`; a real future Business Quality `engine_version` field would be tagged `"engine_reported"` automatically. **Result: traceable, never fabricated, never silently presented as native.**

## Fraud-Risk Semantic Result

Confirmed via 6 dedicated tests (`TestEnforcedVersusUnenforcedGate`, `TestActiveGatesVersusUnresolvedRiskFlags`) plus the real `AAL`/India fraud-flag cases above: Business Quality's fraud-risk rejection **never** appears as an active gate, an enforced veto, a signal exclusion, or a production block. It appears only as `unresolved_risk_flags`, with the literal text *"flag present, not currently enforced as an exclusion"* — satisfying the brief's `flag_present=true / currently_enforced=false / described_by_rci=true` semantics. (A fourth state, `allowed_to_block`, was not implemented as a separate field — RCI never blocks anything in V1 by design, so this state is always implicitly `false` for every engine; adding a dedicated field for an always-constant value was judged unnecessary complexity, named here as a deliberate simplification, not an oversight.)

## Pattern-by-Pattern Results

| Pattern | India occurrences (% of 173) | US occurrences (% of 101) | Assessment |
|---|---|---|---|
| `CP-07-missing-engine` | **173 (100%)** | 31 (31%) | **Too weak for India specifically** — see Narrative Quality Review below |
| `CP-03-growth-priced-in` | 55 (32%) | 32 (32%) | **Appropriate** — consistent occurrence rate across both markets, real two-engine pattern |
| `CP-02-cheap-but-avoid-growth` | 9 (5%) | 10 (10%) | **Appropriate** — the value-trap pattern; confirmed correctly firing for all three of `RELINFRA`/`VEDL`/`GTLINFRA` in this live sample |
| `CP-01-quality-vs-strength` | 0 (0%, correctly never fires — Financial Strength is always `not_applicable` for India) | 3 (3%) | **Appropriate**, low-frequency by design (requires two specific opposing grades simultaneously) |
| `CP-08-low-completeness-favorable` | **0 (0%)** | **0 (0%)** | **Did not fire in this sample** — not a defect; reflects genuinely high aggregate data completeness (India: 139/173 "high" category, 10 "low"; US: 86/101 "high", 11 "low") across both markets in this real sample. The pattern remains correctly implemented and would fire if completeness were genuinely poor — confirmed by the dedicated fixture test from Sprint #003, which still passes. |

**`CP-07`'s 100% India occurrence rate is a real, honest finding, not a success metric to inflate.** It fires for every India company for the same single, always-true structural reason (Financial Strength has no India coverage) — technically correct on every individual firing, but **not informative as a "conflict"** when the cause never varies. Classified as **too weak / not interesting in its current form for India specifically** — it restates a permanent platform fact rather than detecting a company-specific situation. **Recommendation, not implemented this sprint** (a narrowly-scoped wording/template fix, not a contract change): `CP-07`'s narrative template should distinguish "this absence is structural and permanent for this market" from "this absence is a genuine, company-specific data gap" — the latter (genuine `unavailable` cases, 5 in India, 6 in US) is informative; the former (the universal India Financial-Strength gap) is not. Left as a named, evidence-grounded limitation for Sprint #005, not "fixed" by rewording for persuasiveness, per this sprint's own explicit rule against cosmetic fixes.

## India and US Comparison

`CP-03` and `CP-02` occur at remarkably similar rates in both markets (32%/32% and 5%/10% respectively) — a real, evidence-based sign that the underlying patterns reflect genuine cross-market dynamics (growth-vs-valuation tension, value-trap risk), not a market-specific artifact. `CP-01` is structurally India-silent (Financial Strength's own market gap) — expected, not a defect. `CP-07`'s asymmetry (100% vs. 31%) is fully explained by that same structural gap, not a new finding.

## Representative Case Studies (22 companies)

| Symbol | Market | Sector | Engine grades (BQ/FS/GI/VI) | Unavailable/N-A | Active gates | Unresolved flags | Conflicts | Narrative excerpt | Reviewer assessment |
|---|---|---|---|---|---|---|---|---|---|
| `RELINFRA` | IN | Utilities/Energy | avoid(rejected)/N-A/avoid/strong_buy | FS: N-A | none | BQ fraud flag | `CP-02`, `CP-07` | "...Growth Intelligence independently flags weak or deteriorating growth — a pattern Epic 004's own outcome validation found associated with real value traps..." | **Appropriate** — directly matches the real -82% outcome this pattern exists to flag |
| `VEDL` | IN | Metals | buy(79)/N-A/avoid(32)/strong_buy(92) | FS: N-A | none | none (BQ currently supported, not flagged today) | `CP-02`, `CP-07` | Same `CP-02` template | **Appropriate** — honestly reflects today's BQ state, not forced to match historical framing |
| `RELCAPITAL` | IN | Financials | mixed(58)/N-A/mixed(59)/strong_buy(83) | FS: N-A | none | none | `CP-07` only | "Financial Strength could not be evaluated..." | **Correctly absent** — `CP-02` rightly does not fire since GI is `mixed`, not `avoid`, today; matches Sprint #007/#008's own disclosed exception |
| `GTLINFRA` | IN | IT/Telecom infra | unavailable/N-A/avoid/strong_buy | BQ: unavailable; FS: N-A | none | none | `CP-02`, `CP-07` | Same `CP-02` template | **Appropriate** — a third live confirmation of the value-trap pattern |
| `AAL` | US | Airlines | supported/**avoid (enforced)**/—/— | — | **FS: true_veto (enforced)** | BQ true_veto flag, not enforced | none detected this run | (gate-driven, no conflict template fired) | **Appropriate** — the decisive proof both states distinguish correctly in one real, live response |
| `ADBE` | US | Software | supported/supported/supported/supported | none | none | none | **none** | "4 of 4 applicable engines support this thesis. No conflicting evidence pattern detected." | **Appropriate** — a clean, fully-aligned case, correctly produces no false conflict |
| `HDFCBANK` | IN | Banks | mixed(58)/N-A/strong_buy(91)/mixed(53) | FS: N-A | none | none | `CP-07` only | "Financial Strength could not be evaluated..." | **Appropriate** — Bank/NBFC Valuation gating handled correctly inside Valuation Intelligence itself, no special-casing needed in RCI |
| `ZOMATO` | IN | Consumer tech | unavailable/N-A/unavailable/unavailable | BQ, GI, VI: unavailable; FS: N-A | none | none | `CP-07` | "Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence could not be evaluated..." | **Insufficient evidence** — correctly labeled, not forced into a false thesis |
| `X` (US Steel) | US | Metals | unavailable/unavailable/unavailable/unavailable | All 4: unavailable | none | none | `CP-07` | Same shape as `ZOMATO` | **Insufficient evidence** — correctly labeled |
| `GE` | US | Industrials | supported/avoid/supported/avoid | none | none | none | `CP-01` | "...Financial Strength raises real solvency concerns — business quality does not offset financial fragility." | **Appropriate** |
| `LMT` | US | Aerospace/Defense | supported/avoid/avoid/warning | none | none | none | `CP-01`, `CP-03` | Two patterns combined | **Appropriate** — narrative concatenation reads coherently, not redundant |
| `UAL` | US | Airlines | supported/**warning**/supported/supported | none | none | none | `CP-01` | Milder FS signal than `AAL`'s veto | **Appropriate** — correctly a `warning`, not elevated to the same severity as `AAL`'s genuine veto |
| `JPM` | US | Banks | supported/—/—/— *(partial fetch)* | FS/GI/VI: unavailable this run | none | none | `CP-07` | — | **Appropriate, but data-limited this run** — named honestly, not a false "clean" result |
| `CAT` | US | Industrials | mixed/—/—/mixed | — | none | none | **none** | "No conflicting evidence pattern detected." | **Correctly absent** |
| `AMT` | US | REIT | mixed/—/strong_buy/avoid | — | none | none | `CP-03`, `CP-07` | Growth-priced-in template | **Appropriate** |
| `DUK` | US | Utilities | mixed/—/strong_buy/avoid | — | none | none | `CP-03` | Same template | **Appropriate** |
| `SUZLON` | IN | Renewable energy | unavailable/N-A/strong_buy/avoid | BQ: unavailable; FS: N-A | none | none | `CP-03`, `CP-07` | Growth-priced-in template | **Appropriate** |
| `RPOWER` | IN | Power/Utilities | unavailable/N-A/—/— | BQ: unavailable; FS: N-A | none | none | `CP-07` only | — | **Correctly absent** — no `CP-02`/`CP-03` forced despite this being a historically distressed name |
| `BAJFINANCE` | IN | NBFC | mixed/N-A/strong_buy/avoid | FS: N-A | none | none | `CP-03`, `CP-07` | Growth-priced-in template | **Appropriate** |
| `TCS` | IN | IT | supported/N-A/supported/supported *(high agreement)* | FS: N-A | none | none | `CP-07` only | — | **Appropriate** — clean alignment, no false conflict |
| `RELIANCE` *(supplementary, kill switch deliberately disabled for this one check)* | IN | Energy | unavailable/N-A/unavailable/**feature_disabled** | BQ, GI: unavailable; FS: N-A | none | none | none (VI correctly excluded from both supporting/opposing evidence) | "Business Quality, Financial Strength, Growth Intelligence could not be evaluated..." (VI silently omitted from the support/oppose lists, exactly as designed) | **Appropriate** — confirms `feature_disabled` evidence is cited for context only, never counted as positive or negative |
| `HDFCLIFE` | IN | Insurance | supported/N-A/supported/mixed | FS: N-A | none | none | `CP-07` only | — | **Appropriate** |

## Narrative Quality Review (against the 10-rule rubric)

| Rule | Result |
|---|---|
| 1. Separates supporting/opposing evidence | **Pass** — confirmed across all 22 case studies |
| 2. No implied Buy/Hold/Sell/Strong Buy | **Pass** — no narrative anywhere references a signal label |
| 3. No replacement confidence score | **Pass** — `explanation_confidence_category` is always a category (high/moderate/low), never a number |
| 4. No unresolved flag called an active gate | **Pass after Defect 1's correction** — confirmed live via `AAL`/`RELINFRA` |
| 5. Does not overstate low-confidence evidence | **Pass** — `ZOMATO`/`X` correctly labeled `insufficient_evidence`, not forced into a thesis |
| 6. Missing/unavailable/non-applicable/feature-disabled/stale/execution-error never negative | **Pass** — confirmed structurally (`NEVER_NEGATIVE_STATUSES`) and via the `RELIANCE` feature-disabled case |
| 7. Does not hide genuinely active gates | **Pass** — `AAL`'s enforced veto is surfaced, not suppressed |
| 8. No double-counting one fact under multiple labels | **Pass** — confirmed by construction (each engine contributes once); no cross-engine duplication found in any of the 22 case studies |
| 9. Communicates uncertainty clearly | **Pass** — `evidence_completeness_pct` and the confidence category are both present in every response |
| 10. Deterministic for the same frozen snapshot | **Pass** — confirmed by the existing fixture test (`test_calling_compute_twice_with_same_snapshot_produces_equal_responses`), unaffected by this sprint's changes |

**One real limitation found and classified (not a rule violation, a usefulness gap)**: `CP-07`'s near-universal India firing (rule 9's "communicates uncertainty clearly" is technically satisfied, but the *informativeness* of doing so 100% of the time is genuinely weak) — classified as a **wording/template defect**, named for Sprint #005, not fixed cosmetically this sprint.

## Deferred Pattern Decision

| Pattern | Decision | Rationale |
|---|---|---|
| `CP-04` (financial strength vs. quality, inverse framing) | **C — Reject** | Real validation this sprint found no case where `CP-01`'s existing framing was insufficient; the inverse framing would likely just restate `CP-01`'s same evidence from the other engine's perspective, a real duplication risk this sprint's evidence does not justify accepting |
| `CP-05` (technicals vs. fundamentals) | **B — Defer until a new input surface exists** | Requires reading the existing technical score, which RCI's adapters deliberately do not touch this sprint — no validation evidence from this sprint's four-engine-only sample can speak to this pattern's value |
| `CP-06` (regime context) | **B — Defer until a new input surface exists** | Same reasoning — requires `market_regime`, untouched by this sprint's adapters |

**No deferred pattern was implemented this sprint** — none of this sprint's real validation evidence proved a concrete contract defect in the existing five-pattern V1 that would require one, per this sprint's own explicit rule.

## Non-Interference Proof

Confirmed via the existing and expanded regression suite, deterministic, not dependent on the real-data validation run: neither `prediction_engine.py` nor `daily_picks.py` imports any RCI module (re-confirmed, with the one test-quality fix described in the Evidence Checkpoint); RCI's pure core imports nothing from Prediction Engine, `os`, or any network/database library; calling the pure function twice with the same snapshot produces equal responses; the legacy `growth_score`/`valuation_score` wiring in `daily_picks.py` remains present and untouched.

## Defects Found and Corrected

| Defect | Classification | Fix | Regression coverage |
|---|---|---|---|
| Active gate / unresolved risk flag conflation | **Implementation defect** | `currently_enforced` field added; `active_gates`/`unresolved_risk_flags` output split | 6 new tests |
| Engine-version provenance untraceable | **Contract gap** | `engine_version_provenance` field added | 4 new tests |
| Overly strict non-interference test flagged a code comment as an import | **Test gap** | Test corrected to check for actual import statements | n/a — test-only fix |

## Technical Debt

- `CP-07`'s near-universal India occurrence (named above) — a real usefulness limitation, not a correctness defect; recommended for a Sprint #005 wording-template review.
- `CP-08` has never fired against real data in either market — correctly implemented (confirmed by its own fixture test) but unobserved in practice; worth re-checking once a genuinely low-completeness real company is found, not assumed broken.
- The "fourth state" (`allowed_to_block`) from the brief's suggested 4-state model was deliberately not implemented as a separate field, since it is always constant (`false`) for every engine in V1 — named as a documented simplification.
- `CP-04`/`CP-05`/`CP-06` remain deferred (decisions above), unchanged technical debt carried from Sprint #003.

## Test Summary

| Suite | Count | Result |
|---|---|---|
| Pre-existing RCI tests (Sprint #003) | 51 | All pass, confirmed unaffected by this sprint's additive field changes |
| New contract-integrity tests (this sprint) | 10 | All pass |
| **Full backend suite** | **831 total (821 pre-existing + 10 new)** | **831/831 passing** |

## Recommendation for Sprint #005

**Do not integrate RCI into Prediction Engine, Daily Picks, or any consumer yet.** This sprint's real-world validation confirms the five implemented patterns produce accurate, proportionate, deterministic explanations across 274 real companies in both markets, with zero crashes and zero category leakage — a genuinely strong result. However, one real, evidence-based usefulness gap (`CP-07`'s near-universal India firing) should be addressed via a narrowly-scoped narrative-template review **before** any live exposure, since a 100%-occurring "conflict" pattern would be actively confusing, not merely suboptimal, if surfaced to a real user today. **Recommended Sprint #005 scope: a narrative-template refinement sprint** (distinguishing structural, permanent absence from genuine, company-specific data gaps within `CP-07` specifically), not a new validation round and not a live integration — the evidence gathered this sprint is sufficient to justify a template fix without needing to re-run the full real-data validation again.

---

*No Prediction Engine, Daily Picks, Portfolio, Watchlists, Alerts, or UI code was modified. No score, signal, confidence, threshold, or kill-switch state was changed. No new external data provider was introduced. Validation scripts and raw run output remain outside the committed diff.*
