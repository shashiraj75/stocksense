# EPIC-003 — Growth Intelligence — Closure Report

**Status:** Closed. Sprints #001–#008 complete. India confidence-only integration live in `prediction_engine.py`, empirically confirmed not to affect Daily Picks ranking. US remains explainability-only pending a future outcome re-measurement.

**Evidence Checkpoint (performed before any documentation change in this closure sprint):** re-examined every conclusion reached across Sprints #001–#008 for internal consistency. **No contradiction found.** The one notable evolution — Sprint #001's Design Study assumed "roughly half" of the India metric catalogue was unconfirmed; Sprint #002's live Feasibility Study found the real situation materially better — is not a contradiction to silently smooth over. It is the design-study-then-feasibility-study sequence working exactly as intended, and it was already handled transparently at the time: SSDS-007 itself carries explicit "Update" sections pointing to Sprint #002's resolved findings rather than having its original text quietly rewritten. **Epic 003's conclusions remain valid as documented.**

---

## 1. Executive Summary

Growth Intelligence is StockSense360's third intelligence engine (after Business Quality and Financial Strength), answering a question neither of those engines owns: *"is this company's revenue, earnings, and cash flow growing — and is that growth real, durable, and not bought at shareholders' expense?"* Across eight sprints, the engine was designed, found feasible (with real, India-specific data gaps narrower than initially assumed), implemented, calibrated against 246 real companies, outcome-validated against real subsequent price performance, integrated into the Prediction Engine as a narrowly-bounded, India-only confidence signal, and empirically confirmed not to affect Daily Picks' ranking. The defining finding of the epic is asymmetric by design, not by oversight: **India cleared every bar required for live integration; the US market did not**, and the architecture's own kill switch and hard market gate reflect that asymmetry directly rather than papering over it.

## 2. Objectives Achieved

| SSDS-007 objective | Status |
|---|---|
| A dedicated, explainable growth-scoring engine, separate from `quality_factors.py`'s scattered growth logic | **Achieved** — `growth_intelligence_engine.py`, 7 scoring categories, `EngineResponse` contract |
| Provider-independent architecture, reusing the Data Fabric pattern | **Achieved** — confirmed to transfer without modification (Sprint #001's own stated hope, validated true) |
| Cross-market support (India + US) | **Achieved for India** (confidence + explainability); **partially achieved for US** (explainability only, by deliberate, evidence-based design, not a gap) |
| Confidence-only Prediction Engine integration, mirroring Financial Strength's own pattern | **Achieved** — `_apply_growth_intelligence_adjustment`, ±3 cap, live in `prediction_engine.py` |
| Validation before integration, per this engagement's standing discipline | **Achieved and exceeded** — 246 companies (Sprint #004), then outcome-validated against real forward returns (Sprint #005), then 339 companies validated against the actual Daily Picks ranking code (Sprint #008) |

## 3. Architecture Summary

Confirmed unchanged and compliant at closure, per this sprint's explicit Architecture Review requirement:

- **Provider independence**: `growth_intelligence_engine.py` has zero knowledge of yfinance, screener.in, or SEC EDGAR — confirmed by inspection, unchanged since Sprint #003.
- **Adapter pattern**: `india_growth_adapter.py` (raw `fetch_screener_data()` output) and `us_growth_adapter.py` (yfinance `.financials`/`.balance_sheet`) are the only modules with provider knowledge — confirmed unchanged.
- **Confidence-only integration**: `_apply_growth_intelligence_adjustment` has no parameter granting access to `composite_score`/`signal` — confirmed structurally (Sprint #007) and empirically, by proving `ranking_alpha` is bit-for-bit invariant to its output (Sprint #008).
- **India-only numeric influence**: enforced by two independent controls — the hard `market == "IN"` check and the separate `_growth_intelligence_confidence_enabled` kill switch — confirmed each alone suffices (Sprint #007 regression tests).
- **US explainability-only**: confirmed — `growth_intelligence` is computed and exposed in the response dict for US, but `_apply_growth_intelligence_adjustment` returns confidence unchanged for every US case tested (Sprint #007: 117 companies; Sprint #008: 130 companies; zero exceptions across both).
- **Kill switch**: env-var-backed (`GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US`), independent of deployment, independent of Financial Strength (confirmed no comparable mechanism exists to couple with), fails safe on malformed input, documented in `.env.example`.
- **Graceful degradation**: `None`, `REJECTED`, missing-score, and empty-dict inputs all leave confidence unchanged at every layer tested (engine, adapter, Prediction Engine integration, Daily Picks).
- **EngineResponse compliance**: `compute_growth_intelligence()` returns `EngineResponse(...).to_dict()`, the same contract Business Quality and Financial Strength use — confirmed unchanged since Sprint #003.

**No inconsistency found in this review.**

## 4. Growth Intelligence Design Evolution

| Stage | What was believed | What evidence showed | How the gap was handled |
|---|---|---|---|
| Sprint #001 (Design Study) | India data feasibility "roughly half unconfirmed" for the proposed 15-metric catalogue | — | Explicitly named as requiring a feasibility study before implementation — not assumed solvable |
| Sprint #002 (Feasibility Study) | — | Live evidence against 85 real companies: revenue/profit/EPS-trend Excellent/High-confidence market-wide; the real gap is narrower (banks/NBFCs specifically, confirmed via CDSL/MCX/IEX having full data despite being "Financials") | SSDS-007 updated with explicit "Update" sections pointing to the resolved findings; original text preserved as historical record, not deleted |
| Sprint #003 (Engine v1) | Acceleration bonus and CAGR formula assumed correct by design | Two genuine defects found during the sprint's *own* test-writing (acceleration-bonus dead code; a CAGR crash on a negative terminal value) | Both fixed narrowly, each with a dedicated regression test reproducing the exact failure |
| Sprint #004 (Calibration) | Marginal EPS Trend signals assumed harmless when listed as a "strength" | A real explainability defect found in live output (COALINDIA/SRF, both 6/100 "avoid," listing a marginal +3 signal as their only "strength") | Fixed with a new presentation-layer filter (`MIN_NOTABLE_CONTRIBUTION`), not a scoring-weight change |
| Sprint #005 (Outcome Validation) | Assumed (implicitly, pending evidence) that strong growth scores would correlate with strong forward returns in both markets | India: positive, monotonic correlation. **US: negative correlation** in the only well-powered window | Not assumed to be a defect — traced via false-signal analysis to an apparent growth-to-value rotation; named as unresolved (not ruled out as persistent) rather than explained away |
| Sprint #006 (Integration Decision) | — | Synthesized #001–#005 into a market-asymmetric decision | India confidence-only; US explainability-only; both as explicit, evidence-grounded choices, not a compromise for its own sake |

## 5. India Findings

- **Data feasibility**: revenue growth, profit growth, EPS-trend, and growth durability are Excellent/High-confidence across the *entire* India market, including banks (Sprint #002, 85 companies; reconfirmed at 209 companies by Sprint #008).
- **Calibration**: zero scoring-level false positives or false negatives across 123–209 companies tested cumulatively; one explainability defect found and fixed (Sprint #004).
- **Outcome correlation**: positive and monotonic across 3/6/12-month windows (Spearman ρ +0.150 to +0.174, n=119, Sprint #005); strong year-over-year rank stability (ρ=0.751).
- **Integration**: live in `prediction_engine.py`, ±3 confidence cap, validated against 155–209 real companies across Sprints #007–#008 with zero crashes and a real, non-degenerate adjustment distribution.
- **Daily Picks**: ranking provably unaffected; the one real effect (rescuing/sinking an already-borderline stock's eligibility at the 25% confidence floor) is bounded, intended, and empirically characterized, not a side effect discovered after the fact.

## 6. US Findings

- **Data feasibility**: full-depth multi-year statement history via yfinance, sufficient for every implemented metric (Sprint #003).
- **Calibration**: zero scoring-level false positives or false negatives across 117–132 companies tested cumulatively (Sprint #004/#008).
- **Outcome correlation**: **negative** (ρ=-0.437) in the only well-powered window (Sprint #005) — attributed primarily to an apparent growth-to-value rotation (every false signal in the sample traced to a genuine, correctly-measured growth profile caught on the wrong side of that rotation), but **not conclusively ruled out as a persistent characteristic**, given the single-window methodology limitation Sprint #005 named explicitly.
- **Integration**: computed normally (for explainability/telemetry), confirmed to apply **exactly zero** confidence adjustment across every one of 247 companies tested cumulatively (Sprints #007–#008), via two independent, redundant controls.
- **Status at closure**: explainability-only, by deliberate design — not a partially-finished integration.

## 7. Outcome Validation Summary

Sprint #005's single-window proxy methodology (truncating multi-year data by one year, anchoring to the resulting real fiscal date, measuring real subsequent price performance) is the first time any engine in this codebase has had its forward-outcome relationship measured at all — a new validation capability for this engagement, not just a one-off check. The methodology itself revealed a real limitation (India's 12-13 year screener.in depth supported it; US's shallower ~4-5 year yfinance depth did not, confirmed: 111 of 112 "truncated" US scores were artificial rejections, not genuine signal) — handled by pivoting the US analysis to a different anchor, not by silently discarding the negative result it produced.

## 8. Prediction Engine Integration Summary

Sprint #007 implemented Sprint #006's decision exactly: `_get_growth_intelligence` in the existing Round-2 `asyncio.gather`, `_apply_growth_intelligence_adjustment` in the existing confidence chain after Financial Strength's own adjustment, a new independent kill switch, a new ±3 cap (`GROWTH_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP`, distinct from Financial Strength's ±6), and per-evaluation telemetry that never affects the returned value (confirmed by a dedicated test that breaks the logger). Validated against 155 India + 117 US real companies with zero crashes.

## 9. Daily Picks Validation Summary

Sprint #008 confirmed empirically — not just by reading `_zscore_and_rank()`'s source — that `ranking_alpha` (Daily Picks' actual Top-6 sort key) is bit-for-bit invariant to Growth Intelligence's confidence adjustment, across 339 real-company evaluations in both markets. The one real, intended effect (boundary rescue/sink at the 25% confidence floor) was found, measured (8 of 209 India companies when confidence was deliberately sampled near that boundary; zero from a realistic range), and attributed correctly in every case inspected. No string-collision exists between Growth Intelligence's reasoning and the pre-existing quality gate's exclusion checks.

## 10. Testing Summary

| Sprint | New tests | Cumulative full-suite count |
|---|---|---|
| #003 (Engine v1) | 50 | 586 |
| #004 (Calibration) | 3 | 589 |
| #007 (Prediction Engine Integration) | 51 | 635 |
| #008 (Daily Picks Validation) | 10 | 645 |

**645/645 passing at closure.** Every test added was justified by either a genuine defect found during validation (Sprints #003, #004) or a real architectural property requiring empirical, not just structural, proof (Sprints #007, #008) — none were added for coverage's own sake, per this engagement's standing "no artificial coverage" discipline.

## 11. Performance Summary

| Component | Measurement |
|---|---|
| `growth_intelligence_engine.py` (pure computation) | ~0.01ms/call, both markets |
| `india_growth_adapter.py` (already-fetched data) | ~0.008ms/call |
| `us_growth_adapter.py` (DataFrame lookups) | ~1.29ms/call — the one component without its own micro-cache, named as a low-priority future optimization, not a current problem |
| `_apply_growth_intelligence_adjustment` (Prediction Engine layer) | ~0.001ms/call |
| `_zscore_and_rank()` (Daily Picks layer) | ~0.7ms over a 150-stock universe, **unaffected** by Growth Intelligence since that function never reads confidence/growth_intelligence at all |
| India's added cold-path cost | Negligible, confirmed empirically: the screener.in fetch this integration triggers hits the same 4-hour cache `augment_info_with_screener` already populates earlier in the same `predict()` call (0.002ms on the call this integration actually makes) |
| US's added cold-path cost | Zero new network calls — reuses the already-shared ticker object |

**No performance concern at any layer.**

## 12. Explainability Summary

Deterministic (confirmed via repeated-call identity tests), evidence-based (every reason names a real score/grade), free of duplication with Financial Strength's own reasoning (confirmed by direct comparison), free of fabricated entries (zero-adjustment cases produce no reasoning entry; US produces none ever), and free of any string-collision with Daily Picks' pre-existing quality-gate exclusion logic. One genuine defect (a marginal signal misleadingly surfacing as a "strength") was found and fixed during Sprint #004, not missed.

## 13. Production Readiness

| Dimension | Assessment |
|---|---|
| Engine correctness | Sound — zero scoring-level false positives/negatives across 246+ companies |
| Integration correctness | Sound — confirmed structurally and empirically at both the Prediction Engine and Daily Picks layers |
| India numeric integration | **Production-ready**, live |
| US numeric integration | **Not production-ready** — explicitly withheld pending outcome-correlation re-measurement, not a partial/buggy state |
| Performance | No measurable concern |
| Test coverage | 645/645 passing, every addition evidence-justified |

## 14. Accepted Technical Debt

Named explicitly, per this engagement's standing practice of treating remaining limitations as understood and tracked, not hidden:

1. **US outcome correlation remains unresolved** — the single most important open item. Re-measurement requires calendar time to pass for non-clustered fiscal-anchor windows (most current US anchors share a Dec-2025 fiscal year-end).
2. **Reinvestment Efficiency's invested-capital calculation is exposed to corporate-action distortion** — confirmed concretely for RELIANCE (a real bonus-issue jump falls inside its 3-year lookback window), not yet given corporate-action-aware handling.
3. **BANDHANBNK-style scraper gap** — one India bank returned zero core growth fields despite a successful fetch, landing in `REJECTED` rather than the other banks' graceful degraded-confidence path; root cause unconfirmed.
4. **Margin Trend validated only by spot-check (5 symbols), not the full India sample** — the scraper addition that enables it (`opm_annual_pct`) postdates the bulk of this epic's validation data.
5. **`us_growth_adapter.py`'s DataFrame lookups have no micro-cache** — the one component whose per-call cost (~1.29ms) is meaningfully higher than every other piece, though still negligible in absolute terms.
6. **Organic-vs-acquisition growth and Guidance Consistency remain `[UNAVAILABLE]`** — no data source exists for either in any market evaluated; not a gap discovered late, a boundary respected throughout per SSDS-007's own original scoping.
7. **No outcome-validation methodology exists yet for re-measuring US with non-clustered anchors** — Sprint #005 named the need; building it is future work, not yet started.

## 15. Lessons Learned

- **A design study's assumptions should be evidence-tested before being trusted, even when (especially when) they sound reasonable** — Sprint #001's "roughly half unconfirmed" framing for India was a defensible, honest estimate at the time, and turned out to be more pessimistic than reality; the lesson is not "estimate better," it's "the feasibility-study step paid for itself exactly as the engagement's own discipline predicts it should."
- **Outcome validation is a different question from scoring correctness, and conflating them would have been a mistake** — Sprint #004 (zero false positives/negatives) and Sprint #005 (negative US correlation) are both true simultaneously; the engine measures growth correctly, and growth's relationship to forward returns is regime-dependent. Treating Sprint #005's finding as "the engine is broken" would have been wrong; treating it as "nothing to worry about" would have been equally wrong. The market-asymmetric integration decision (Sprint #006) is the direct, correct consequence of holding both truths at once.
- **A validation methodology can itself have limitations worth discovering** — Sprint #005's truncation approach silently would have produced garbage for the US market if its failure (111/112 artificial rejections) hadn't been caught and reported as a finding in its own right, rather than averaged away or ignored.
- **Empirical proof is stronger evidence than structural argument, even when the structural argument is correct** — Sprint #007/#008 could have stopped at "the function signature has no access to ranking internals" (true, and sufficient as far as it goes) but instead proved `ranking_alpha` is bit-for-bit identical with/without the integration across hundreds of real companies — a categorically stronger claim, and the one this closure report can actually stand behind.
- **A small, capped, well-gated integration is the right shape for genuinely uncertain evidence** — the ±3 cap, the dual India/kill-switch gates, and the explainability-without-numeric-influence US path are all direct expressions of "the evidence supports a modest, reversible action, not a large or hard-to-undo one."

## 16. Recommendations for Epic 004

Per the Master Roadmap's own existing sequencing (Valuation Intelligence is the next proposed epic): Growth Intelligence's own outcome-validation finding is directly relevant to Epic 004's scoping. A company's growth score, by itself, was shown (Sprint #005) to have a regime-dependent relationship to forward returns — partly because growth-style companies often carry growth-priced valuations, and a value/growth rotation can dominate a pure-growth signal's predictive power. **Valuation Intelligence, once built, is a natural pairing for Growth Intelligence** (a "growth at a reasonable price" combined signal is a well-established equity-research concept) — named here as a future integration opportunity, not a commitment this closure report is authorizing. Epic 004 should proceed with its own Design Study and feasibility study, exactly as Epic 002 and Epic 003 both did, rather than skipping straight to implementation on the strength of this observation alone.

## 17. Final Epic Rating

**Strong delivery, narrowly and honestly scoped.** Every sprint's deliverable matched its brief; every genuine defect found was fixed narrowly with regression coverage; the one major open question (US outcome correlation) was investigated rigorously, found genuinely unresolved, and handled with a real architectural control (the market gate) rather than an assumption in either direction. The epic's defining strength is that its final state — India live, US explainability-only — is not a "we ran out of time" compromise; it is the evidence's own conclusion, implemented exactly as the evidence supports and no further.

## 18. Complete Commit Timeline

| Sprint | Commit | Summary |
|---|---|---|
| #001 — Design Study | `0caf119` | SSDS-007 specification, 15-metric catalogue, provider evaluation |
| #002 — India Feasibility Study | `9668fe3` | Live evidence against 85 real companies; India situation better than assumed |
| #003 — Engine v1 | `d66ad19` | `growth_intelligence_engine.py` + both adapters; 2 genuine defects found and fixed |
| #004 — Calibration | `dccb172` | 246-company validation; 1 explainability defect found and fixed |
| #005 — Outcome Validation | `d8e9f2b` | Real forward-return correlation; India positive, US negative |
| #006 — Integration Decision | `568921e` | India-only confidence, ±3 cap, US explainability-only — the binding decision |
| #007 — Prediction Engine Integration | `8cabd59` | `_apply_growth_intelligence_adjustment` live; 155+117 companies validated |
| #008 — Daily Picks Validation | `c006dc9` | Empirical proof of ranking invariance; 209+130 companies validated |
| #009 — Epic Closure (this document) | *recorded below* | — |

---

## Final Readiness Assessment

**India: production-ready, live.** **US: explainability-only by design, not yet ready for numeric integration** — the specific, named evidence threshold for reconsidering this (non-clustered anchor windows, non-negative correlation at n≥80, a completed US Stability Review) remains open and is the natural next sprint, not a blocker to closing this epic.

## Technical Debt Register

See Section 14 in full — 7 items, each named with its specific resolution path, none silently dropped.

## Roadmap Updates

`INDEX.md` and `MASTER-ROADMAP.md` updated in this same commit to mark Epic 003 closed and to point to this closure report as the canonical record, mirroring EPIC-001/EPIC-002's own precedent — future sessions should read this document, not every individual sprint report, to understand what was built.

## Recommendation for Epic 004

Proceed with Valuation Intelligence's own Design Study, informed by (but not pre-committed to) Growth Intelligence's outcome-validation finding about growth/valuation regime interaction — per Section 16.

## GitHub Actions Status

No production code was modified by this closure sprint — no new CI run applicable (documentation-only, consistent with this engagement's established pattern for docs-only sprints' path-filtered CI behavior). The full backend suite (645/645) was re-confirmed locally before this report was written.

## Final Commit Hash

Recorded below, after this closure sprint's commit.

---

*This is the permanent closure record for Epic 003. A future session should read this document, not every individual Sprint #001–#008 report, to understand what was built, why, how it was validated, and what remains intentionally out of scope.*
