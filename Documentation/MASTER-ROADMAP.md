# StockSense360 — Master Roadmap

**Purpose of this document:** the single executive entry point for the entire platform. A new engineer, or a future AI session with no prior context, should be able to read this document alone and understand what StockSense360 is, what exists today, what is being built next, and where to find the detailed record of everything that came before.

**Status legend used throughout:** **Completed** · **In Progress** · **Specification** · **Planned** · **Future** — applied strictly; nothing below is marked Completed unless it is live in production code today, evidenced by an actual file, test suite, or closure document.

---

## Section 1 — Vision

StockSense360 is an **AI-powered investment intelligence platform**, not a traditional stock screener. The distinction is deliberate and structural, not marketing language:

- **Explainable AI.** Every recommendation traces to named, inspectable factors — a score, a category breakdown, a stated reason for any rejection. Per SSDS-000's confirmed architecture, this explainability is rule-based and deterministic today, not LLM-generated; nothing in the platform produces a judgment a user (or engineer) cannot trace back to a concrete metric.
- **Multi-engine architecture.** Rather than one monolithic scoring function, the platform is decomposing into independent, provider-independent intelligence engines (Business Quality, Financial Strength, and others planned) that each answer one well-defined question and can be validated, tested, and evolved in isolation — proven viable by Epic 001's delivery across two markets with a single unmodified engine.
- **Cross-market support.** India (NSE) and US markets are first-class, not India-with-a-US-afterthought or vice versa — each with its own data adapter, but sharing every engine's scoring logic unmodified.
- **Evidence-based recommendations.** Every engineering decision in this platform's recent history — what to build, what to fix, what to defer — has been settled by live data validation, not assumption. This is now a standing engineering discipline (Section 5), not a one-time practice.
- **Long-term maintainability.** Centralized thresholds, structured logging, a typed engine-response contract, and a four-category test suite exist specifically so future changes are verifiable rather than speculative.

---

## Section 2 — Platform Architecture

Nine intelligence domains are named here, each described accurately as it exists **today** — several are live engines, several are existing behavior not yet engine-ized, and several are not yet started. This section does not overstate progress; see Section 3 for exact status.

**Business Quality Intelligence — Completed.** Answers "is this fundamentally an outstanding business worthy of long-term ownership?" — profitability and capital efficiency, balance sheet strength, earnings quality, capital allocation discipline, and durable competitive position, each sector-adapted. Implemented as `backend/services/business_quality_engine.py`, live for both India and US markets, with a hard fraud/distress gate independent of the category score.

**Financial Strength Intelligence — Completed (Epic 002, closed).** Answers a deliberately different question from Business Quality: "could this company survive a downturn and service its obligations?" — liquidity adequacy, leverage trend, debt-servicing capacity, and the Financial Stress Simulation's Earnings Shock scenario. Implemented as `backend/services/financial_strength_engine.py`, live for US, non-FINANCIAL, non-REAL_ESTATE equities, integrated into `PredictionEngine` and Daily Picks as a bounded, additive confidence signal — see [EPIC-002 Closure](Engineering-Handbook/EPICS/EPIC-002-Financial-Strength-Intelligence-Closure.md).

**Growth Intelligence — Future (not yet a dedicated engine).** Growth metrics (sales/profit growth, multi-year trends) exist today only as scattered inputs inside `quality_factors.py` and the Multibagger scorecard's screening logic — not as an independent, explainable engine with its own scoring contract.

**Valuation Intelligence — Future (named direction, not yet built).** Today's valuation logic is a single static P/E cutoff inside the Multibagger scorecard; moving to sector-relative or growth-adjusted (PEG-style) valuation bands has been a named direction in the engineering roadmap for some time but has no dedicated engine or specification yet.

**Risk Intelligence — Future (logic exists, scattered; no dedicated engine).** Risk-related logic exists today in multiple disconnected places — regime clustering, a risk/reward calculation, the quality gate's risk penalty — but there is no single, explainable Risk Intelligence engine that owns this question end-to-end.

**Recommendation Intelligence — Completed, in its current (pre-engine-architecture) form.** The Prediction Engine (`prediction_engine.py`) and the Ranking & Filtering logic already synthesize technical, fundamental, and sentiment factors into a BUY/HOLD/SELL signal today, and have for longer than the new engine architecture has existed. This is the platform's oldest and most mature capability — the open question for later epics is *whether and how* it should evolve to consume Business Quality/Financial Strength/Growth/Valuation/Risk as first-class inputs, not whether a recommendation capability exists at all.

**Portfolio Intelligence — Future, not implemented.** Maps to what the architecture documentation already names "Portfolio Copilot": confirmed, by direct repository search, to not exist today. The portfolio optimizer currently diversifies only a day's new candidates against each other — it has no visibility into anything a user already owns. This remains the single largest confirmed product gap in the platform.

**AI Research Analyst — Future as a distinct module; the underlying capability already exists under a different name.** A conversational, analyst-styled explanation layer does not exist. The underlying capability it would build on — explaining a recommendation in plain language — already exists today as the **Explainability Layer** (`case_generator.py`, explicitly rule-based, no LLM), and should not be confused with it.

**Daily Picks Intelligence — Completed, as a product feature; not a separate engine.** Daily Picks is a live, shipped feature (top 6 BUY ideas per horizon, both markets, automated daily generation) — but architecturally it is a **consumer** of the Recommendation Intelligence pipeline above, not an independent intelligence engine. No "Daily Picks Engine" is being planned as a fourth/fifth named engine; Daily Picks will benefit automatically as the engines above mature.

---

## Section 3 — Epic Roadmap

| Epic | Name | Status | Dependencies | Completion Criteria | Next Milestone |
|---|---|---|---|---|---|
| **001** | Business Quality Intelligence | **Completed** | None | Engine implemented, live-validated (55-company and 65-company live studies), integrated into a first consumer (Multibagger, both markets), 262/262 tests passing, GitHub Actions green. All met — see [EPIC-001 Closure](Engineering-Handbook/EPICS/EPIC-001-Business-Quality-Intelligence-Closure.md). | Closed. No further action unless a named technical-debt item (Beneish M-Score, Altman financial-sector exemption) is prioritized. |
| **002** | Financial Strength Intelligence | **Completed** | Epic 001 (architecture pattern reused) | SSDS-005 finalized; US data-feasibility study completed (India: feasibility-only, deferred); engine implemented and live-validated (76-company live studies); first consumer integrated (`PredictionEngine`, Daily Picks); 442/442 tests passing, GitHub Actions green. All met for the scoped US, non-FINANCIAL, non-REAL_ESTATE surface — see [EPIC-002 Closure](Engineering-Handbook/EPICS/EPIC-002-Financial-Strength-Intelligence-Closure.md). | Closed. No further action unless a named technical-debt item (FINANCIAL/REAL_ESTATE sector support, India implementation, UTILITIES_ENERGY soft-scoring recalibration) is prioritized. |
| **003** | Growth Intelligence | **Completed** | Epic 002 (shared adapter/validation pattern) | A dedicated, explainable growth-scoring engine exists, separate from the scattered logic in `quality_factors.py` today. Engine implemented and live-validated (246-company calibration, then outcome-validated against real forward returns); first consumer integrated (`PredictionEngine`, India confidence-only); Daily Picks ranking empirically confirmed unaffected (339-company validation); 645/645 tests passing, GitHub Actions green. All met for the scoped India confidence-only + US explainability-only surface — see [EPIC-003 Closure](Engineering-Handbook/EPICS/EPIC-003-Growth-Intelligence-Closure.md). | Closed. No further action unless a named technical-debt item (US outcome-correlation re-measurement, Reinvestment Efficiency's corporate-action exposure, the BANDHANBNK scraper gap) is prioritized. |
| **004** | Valuation Intelligence | **Completed** | Epics 002 and 003 (shared adapter/validation pattern) | A dedicated, explainable valuation-scoring engine exists, answering "is this stock trading below, near, or above fair value?" — a question none of the prior three engines own. Engine implemented and live-validated (406-company calibration, then outcome-validated against real forward returns); integrated into `PredictionEngine` as a confidence-only signal in **both** markets with an asymmetric +2/-4 cap and cross-engine safeguard gate; Daily Picks ranking empirically confirmed unaffected (361-company validation); 770/770 tests passing. All met for the scoped V1 metric set — see [EPIC-004 Closure](Engineering-Handbook/Releases/EPIC-004-Valuation-Intelligence-Closure.md). | Closed. Kill switches currently disabled by default in both markets — a short operational decision on activation (not a new validation sprint) is the one open item before Epic 005 should be considered fully unblocked, named explicitly in the closure report. |
| **005** *(proposed numbering)* | Recommendation Consolidation Intelligence | **In Progress — Evidence Summary frontend implemented, dormant (RCI flag remains disabled in Railway)** | Epics 001–004 (synthesizes their existing, validated outputs — adds no new provider data) | Four validated engines' outputs are synthesized into one transparent, explainable investment thesis per stock, replacing today's opaque accumulation of small confidence nudges. | Sprints #001–#011 complete; Sprint #012 ([Live Stock Analysis Frontend Implementation](Engineering-Handbook/Releases/Sprint-012-Recommendation-Consolidation-Live-Stock-Analysis-Frontend-Implementation.md)) implements and wires in the Evidence Summary component (`frontend/src/components/EvidenceSummary.tsx` + a new `DisclosurePanel` primitive + the `RecommendationConsolidation` TypeScript contract in `api.ts`) below the AI Signal card, above the horizon tabs. **Two real findings disclosed, not worked around**: the backend has no field for a "feature-disabled engine" notice (renders nothing for that state, same as full RCI absence); the frontend has no test framework at all today, so validation used `tsc --noEmit`, a full production build, and direct script execution of the core logic against the brief's mocked scenarios, instead of a committed test suite. RCI remains disabled in Railway — the component is implemented but renders nothing in production. **Recommendation: a visual-QA-and-tooling sprint next, not a Railway flag enable.** 886/886 backend suite still passing (no backend changes this sprint). |
| **006** *(proposed numbering)* | Recommendation Intelligence Consolidation | **Future** | Epics 002–005 | The existing Prediction Engine evolves to consume Business Quality, Financial Strength, Growth, Valuation, and Risk as first-class engine inputs, rather than its current internal ad hoc factor blend. | Not started — explicitly sequenced after the component engines exist, per the same "validate the parts before integrating" discipline used throughout Epic 001. |
| **007** *(proposed numbering)* | Portfolio Intelligence | **Future** | Epic 006 (needs a stable, consolidated recommendation signal to be portfolio-aware about) | Concentration-risk and correlation-aware recommendations against a user's actual holdings — the "Portfolio Copilot" capability, confirmed not to exist today. | Not started — requires product scoping before any engineering, per the existing engineering roadmap's own assessment. A four-sprint plan (Portfolio Foundation, Paper Trade Foundation and Daily Picks Sync, Paper Trade Auto-Trigger/Simulated Execution/Notifications, Post-Trade Analysis) is documented in [Section 11](#section-11--epic-007-sprint-plan-portfolio-intelligence). |
| **008** *(proposed numbering)* | AI Research Analyst | **Future** | Epic 006/007 (needs richer, consolidated explainability data to be grounded in) | A true conversational analyst layer, distinct from today's rule-based Explainability Layer. | Not started — deliberately last; explicitly gated on richer explainability data existing first. |
| **009** *(proposed numbering)* | Mutual Fund Intelligence & Portfolio-Fit Support | **Planned / Not Started** | Portfolio and Watchlist Intelligence (Epic 007) — fund-fit analysis requires the platform to have portfolio-context awareness before it can evaluate overlap, concentration, and suitability against what a user already holds. | Fund discovery, quality and risk analysis, portfolio-fit overlap detection, goal-based suitability assessment, explainable fund summaries, and monitoring alerts. Evaluated on quality, consistency, risk, cost, concentration, overlap, and goal alignment — not on recent return alone. No generic "best fund" claims. | Not started. Sequenced after Portfolio Intelligence ships. Exact timing subject to data-provider feasibility, regulatory constraints, and evidence from prior phases. See [Section 10](#section-10--future-product-area-mutual-fund-intelligence--portfolio-fit-support) for full scope. |
| — | Daily Picks Intelligence | **Completed** (as a product feature; not a separate epic) | Recommendation Intelligence (Epic 006 will indirectly improve it) | N/A — already shipped. | No dedicated epic planned; improves automatically as upstream engines mature. |

---

## Section 4 — Current Status

**Completed:**
- Epic 001 — Business Quality Intelligence, both markets, closed.
- Epic 002 — Financial Strength Intelligence, US (non-FINANCIAL, non-REAL_ESTATE) market, closed — see [EPIC-002 Closure](Engineering-Handbook/EPICS/EPIC-002-Financial-Strength-Intelligence-Closure.md).
- **Epic 003 — Growth Intelligence, India confidence-only + US explainability-only, closed** — see [EPIC-003 Closure](Engineering-Handbook/EPICS/EPIC-003-Growth-Intelligence-Closure.md). Live in `prediction_engine.py` for India; empirically confirmed not to affect Daily Picks ranking in either market (339-company validation); US numeric integration explicitly withheld pending a future outcome-correlation re-measurement, named as accepted technical debt, not a gap.
- **Epic 004 — Valuation Intelligence, confidence-only in BOTH markets, closed** — see [EPIC-004 Closure](Engineering-Handbook/Releases/EPIC-004-Valuation-Intelligence-Closure.md). Asymmetric +2/-4 confidence cap with a cross-engine safeguard gate implemented in `prediction_engine.py`; empirically confirmed not to affect Daily Picks ranking in either market (361-company validation); kill switches currently disabled by default in both markets, named as an open operational decision, not a gap in the engineering work itself.
- Recommendation Intelligence (Prediction Engine + Ranking & Filtering) and Daily Picks, as pre-existing, mature platform capabilities — not products of the Epic-numbered engine architecture, but real and operational.

**Cross-Platform Workstreams (separate from the Epic-numbered engine architecture):**
- **Product Integrity Workstream #001 — Data Freshness and Display Consistency Audit, complete.** Triggered by a reported header-clock-vs-Daily-Picks timestamp inconsistency; root-caused to the global header clock silently defaulting to the browser's local timezone while formatted to look like IST, with no label. Fixed (header clock, Stock Analysis and Multibagger "Updated" labels, an added IST/ET disclosure on Daily Picks) — all additive, presentation-only changes; no investment-decision logic, backend code, or Railway configuration touched. India's "2 AM IST" and US's "6:00 PM IST" Daily Picks schedule statements were verified, by direct cron arithmetic, to already be accurate. See [the full report](Engineering-Handbook/Releases/Product-Integrity-001-Data-Freshness-and-Display-Consistency-Audit.md).
- **Product Integrity Workstream #001B — US Daily Picks Runtime Verification, complete.** Confirmed via live, read-only production API calls that the US Daily Picks batch genuinely did not run on Monday, 29 June 2026, despite its 12:30 UTC (6:00 PM IST) trigger being correctly scheduled — India's same-day run succeeded on the same infrastructure, ruling out a platform-wide cause. The exact upstream reason (GitHub Actions vs. delivery-to-Railway failure) is an honestly-named, unresolved observability gap (no `gh`/Railway log access from this environment). Added a minimal, in-memory `last_trigger_received_at` marker (`services/daily_picks.py`, exposed via `/api/picks/status`) so the next occurrence is directly diagnosable. No cron, schedule, Railway variable, or investment logic changed. 889/889 full suite passing. See [the full report](Engineering-Handbook/Releases/Product-Integrity-001B-US-Daily-Picks-Runtime-Verification.md).
- **Product Integrity Workstream #001C — US Daily Picks Trigger-Delivery Investigation, complete (investigation-only, no code change).** A real trigger was observed reaching Railway for US mid-session, proving the delivery path works when invoked; the original Monday miss's exact GitHub-Actions-side cause remains unconfirmed. See [the full report](Engineering-Handbook/Releases/Product-Integrity-001C-US-Daily-Picks-Trigger-Delivery-and-Recovery.md).
- **Product Integrity Workstream #002A — India Yahoo Symbol Failures and Batch-Isolation Verification, complete.** Investigated `.NS` yfinance errors for US-universe tickers (`BRC`/`SSB`/`STRA`) found in Railway logs; exhaustive code tracing and three live screener reproductions found no contamination mechanism — left honestly unresolved, with an exact instruction for the log context that would close it next time. **Found and fixed a separate, fully-confirmed defect**: Yahoo's screener now caps `count` at 250, but the code requested 1000, silently failing on every run and falling back to the full, unfiltered stock universe — fixed to `count=250`. Confirmed existing per-ticker/per-batch error isolation already correctly prevents one bad ticker from failing a whole batch. 892/892 full suite passing; no ranking, signal, confidence, RCI, Valuation, or schedule logic changed. See [the full report](Engineering-Handbook/Releases/Product-Integrity-002A-Daily-Picks-India-Symbol-and-Batch-Isolation-Verification.md).

**In Progress:**
- **Epic 005 — Recommendation Consolidation Intelligence, Sprints #001–#008 complete.** Sprints #001–#006 built, validated, refined, and selected the integration path. Sprint #007 found a real cache-mutation risk and selected a dedicated, read-only response composer as the safe boundary. **Sprint #008** ([report](Engineering-Handbook/Releases/Sprint-008-Recommendation-Consolidation-Live-Stock-Analysis-API-Implementation.md)) implemented that composer behind a new, disabled-by-default `RCI_LIVE_STOCK_ANALYSIS_ENABLED` flag — invoked from exactly one call site in `/predict`'s cache-hit branch. **Cache-safety proven directly against the real, shared `_pred_cache`**: a regression test populates the actual cache, composes, and confirms it is byte-for-byte unchanged, plus a test simulating Daily Picks' own exact access pattern. **Live spot-check (20 real companies, 10 India + 10 US)**: all base values unchanged, zero cache leakage, `AAL` re-confirms the dual enforced-gate/unresolved-flag proof live, today. **A genuine new finding, named honestly**: `RELINFRA`/`VEDL` did not trigger the value-trap pattern in this live check because Valuation Intelligence's kill switch is genuinely disabled — RCI's most evidence-validated patterns are currently dormant in true production. 38 new tests, 886/886 full suite passing. Flag not enabled in any committed configuration.

**Next Up:**
- A short, separately-scoped review of whether Epic 004's own Valuation Intelligence kill-switch activation decision should be revisited, now that Sprint #008 has shown RCI's own value partly depends on it — new evidence this sprint surfaced, not a decision made here.
- A future Daily Picks Structured Evidence Persistence Design Study (Path B) — real, valuable, deferred, not abandoned; would need its own schema-design and migration-strategy work given the genuine complexity Sprint #006 found.
- An operational decision on activating `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US` in production, and wiring the cross-engine gate's hit-rate monitoring (validated analytically, not yet operationalized) into real telemetry — a parallel, not blocking, item per EPIC-004 Closure's own recommendation.
- Sector-relative percentile's own feasibility question (does a sector-benchmark/peer-aggregation data source exist or need building?) — separate from Epic 004's already-confirmed raw-ratio availability.
- Lower priority, can run independently of Epic 005: Growth Intelligence's accepted technical debt — re-measure the US outcome correlation once enough calendar time has passed for non-clustered anchor windows; corporate-action handling for Reinvestment Efficiency's invested-capital calculation; the BANDHANBNK-style scraper gap; a full-sample Margin Trend re-validation.
- Reuse the now-four-times-proven Data Fabric pattern (provider adapter → resolution → engine adapter → pure engine) rather than re-deriving an architecture a fifth time — Epic 004 already confirms this pattern transfers without modification.

**Future:**
- **Risk Intelligence** — deferred, not rejected, pending Recommendation Consolidation's own findings about which risk dimensions are genuinely missing, and pending confirmed data/NLP feasibility for its most distinguishing sub-areas (litigation, concentration, regulatory) — see the decision document's Special Analysis section.
- **Macro/Market-Regime Intelligence** — deferred; blocked by an already-confirmed Data Fabric gap (no aggregated index-level valuation/rate history), named originally in Valuation Intelligence's own Sprint #001.
- **Portfolio Intelligence** — deferred; structurally downstream of Recommendation Consolidation (needs clean, synthesized per-stock theses to aggregate).
- **Epic 009 — Mutual Fund Intelligence & Portfolio-Fit Support** — Planned / Not Started; sequenced after Portfolio and Watchlist Intelligence ships. Requires portfolio-context awareness before fund overlap and suitability analysis are meaningful. Will evaluate funds on quality, consistency, risk, cost, concentration, overlap, investor suitability, and goal alignment — not on recent return alone. Subject to data-provider feasibility and regulatory constraints at the time of scoping. See [Section 10](#section-10--future-product-area-mutual-fund-intelligence--portfolio-fit-support) for full planned scope.
- **AI Research Analyst** — deferred, lowest priority; no clean way to validate against real outcomes with this engagement's own established evidence-validation discipline.

---

## Section 5 — Engineering Principles

These are not aspirational — each was tested under real pressure during Epic 001 and held:

- **Evidence over assumptions.** Every scope and threshold decision is validated against live data before being trusted. Sprint #006 reversed an initial assumption (India would need a new data provider) by testing it; the Production Readiness Validation found defects unit tests alone had missed.
- **Explainability first.** Every score decomposes into named category contributions; every rejection states a specific reason. This was the design goal from SSDS-003 onward and is what made root-causing real defects (e.g., the HON/ORCL false positive) possible rather than guesswork.
- **Provider independence.** Engines never know about screener.in, yfinance, or BSE as concepts — only a shaped input dict. Proven to generalize across two markets with zero engine changes.
- **Adapter architecture.** Market-specific data shape, units, and derivations are absorbed at a thin adapter boundary, never inside the engine itself. One new ~180-line adapter file was sufficient to add India support to an engine originally built for US.
- **Validate before integrate.** No engine is wired into a production consumer before a live-data validation pass confirms it behaves correctly on real, named companies — including deliberately adversarial cases (known-distressed companies that should score low).
- **Fail-soft engineering.** Every layer degrades to "missing field" or "skip this symbol" on absence or error, never a crash that takes down a whole refresh cycle — verified directly at multiple points, not assumed.
- **Incremental delivery.** Scope limitations are named explicitly when they happen (Sprint #005 being "US-only" was stated as a finding, not buried), which is what made the later India-specific sprint possible to scope correctly.
- **Backward compatibility.** Every additive change in Epic 001 — new columns, new scorecard fields, new engine — preserved every pre-existing test and consumer behavior unchanged; this is checked, not just intended.

---

## Section 6 — Documentation Map

| Category | Location | Purpose |
|---|---|---|
| **EPICS** | `Engineering-Handbook/EPICS/` | The permanent closure record for each completed epic — the *only* document a future session should need to read to understand a finished epic, replacing every individual sprint report for that epic. |
| **SSDS** (System Design Specifications) | `Engineering-Handbook/SSDS/` | The formal specification for any new engine or platform-spanning design (SSDS-000 is the master architecture; SSDS-003 is Business Quality's spec; SSDS-005 will be Financial Strength's). Written *before* implementation, not after. |
| **SES** (Engineering Standards) | `Engineering-Handbook/SES/` | The standing rules every sprint follows regardless of what it builds — coding standards, testing standards, documentation standards, branding. Read once per session, not once per sprint. |
| **Architecture Studies** | `Engineering-Handbook/Architecture/` | Audits, design studies, and validation reports that inform a future SSDS but are not specifications themselves — e.g. the India Fundamentals Data Validation Study, the Financial Strength Intelligence Design Study. |
| **Validation Reports** | Also under `Architecture/` (not a separate folder today) | Live-data validation passes — production-readiness checks, re-validations — kept alongside design studies since both are evidence-gathering artifacts, distinct from binding specifications. |
| **Glossary** | `Engineering-Handbook/Glossary/` | The canonical name for every engine, score, and feature. No document anywhere should invent an alternative name for a concept already named here. |
| **This document** | `Documentation/MASTER-ROADMAP.md` | The single executive entry point — read this first, then follow its links into the categories above as needed. Distinct from `Engineering-Handbook/ROADMAP.md`, which is an older, sprint-numbered engineering backlog predating the Epic structure (see note below). |

**Note on the pre-existing `Engineering-Handbook/ROADMAP.md`:** that document is a detailed, sprint-level plan written before the Epic structure existed. Some of its items were later delivered in a different shape through Epic 001 (e.g., its "wire Altman Z-Score into a reject signal" and "fraud-risk heuristic" items are now satisfied by the Business Quality Engine's hard gate and Beneish M-Score input). It has not been rewritten or reconciled as part of this roadmap — it remains a useful historical backlog for items not yet absorbed into an Epic (notably its Phase 3 Portfolio Intelligence and Phase 4 Explainability items), but this Master Roadmap, not that document, is the current source of truth for what's next.

---

## Section 7 — Development Lifecycle

The standard lifecycle every future epic follows, proven across Epic 001's five sprints:

```
Design Study
     ↓
SSDS Specification
     ↓
Implementation
     ↓
Validation
     ↓
Consumer Integration
     ↓
Epic Closure
```

- **Design Study** — scope the problem, draw boundaries against existing engines, propose architecture, before committing to anything. (Financial Strength Intelligence is here today.)
- **SSDS Specification** — formalize the design study into a binding specification with real thresholds and a complete output contract.
- **Implementation** — build per the specification, additively, with tests in the same commit as every new capability, never after.
- **Validation** — live-data validation against real, named companies, deliberately including adversarial cases, before any consumer touches the new engine.
- **Consumer Integration** — wire into a first production consumer, scoped narrowly, with before/after evidence that nothing pre-existing changed behavior.
- **Epic Closure** — a single permanent closure document, written once the epic's objective is genuinely met, that lets a future engineer skip every individual sprint report.

---

## Section 8 — Long-Term Goals

- **Global market coverage** — India and US today; the adapter pattern is designed to extend to additional markets without engine changes, though this has not yet been attempted for a third market.
- **Portfolio Intelligence** — closing the platform's largest confirmed gap: recommendations that account for what a user already owns.
- **Institutional-grade research** — explainability and validation rigor that matches, not just resembles, professional equity research standards (the explicit aspiration behind the Business Quality Engine's design from SSDS-003 onward).
- **Explainable AI** — remaining deterministic and rule-based even as capability grows; an AI Research Analyst, when built, must explain using the same grounded, traceable factors the rest of the platform already produces, not introduce opaque judgment.
- **Automated Daily Picks** — already live; should improve automatically, without its own dedicated engineering, as Recommendation Intelligence consolidates richer engine inputs.
- **Multi-asset support** — equities only today (plus tracking-only support for Gold/Silver/Crypto in the Watchlist); no engine currently scores non-equity assets.
- **Continuous validation** — live-data validation as a standing practice, not a one-time gate, per the lesson Sprint #007 surfaced (a validation pass found a defect in a module it wasn't even testing).

---

## Section 9 — Success Metrics

- **Engine validation quality** — every new engine validated against a real, named-company universe (50+ companies per market, including deliberately adversarial known-distressed and known-strong cases) before any consumer integration, exactly as Epic 001 established.
- **Test coverage** — every new capability ships with unit, integration, regression, and golden tests in the same commit; 262/262 currently passing platform-wide.
- **Explainability** — every score must decompose into named, inspectable factors; every rejection must state a specific reason. No future engine should ship without this.
- **Cross-market consistency** — a new engine is not considered complete for "both markets" until each market has its own live validation evidence — never assumed to transfer from one market's results to the other.
- **Platform stability** — GitHub Actions green at every code-bearing milestone; zero regressions to pre-existing consumer behavior, checked explicitly, not assumed.
- **User trust** — measured indirectly today via the existing thumbs-up/down feedback and NPS infrastructure; a future goal is connecting that signal back into engine validation rather than leaving it as unused raw data (named already in the pre-existing engineering roadmap).

---

## Recommendation on Epic 005

**Superseded note:** this section originally tracked Epic 004's own recommendation while that epic was still in progress (its Design Study had just completed). Epic 004 is now formally closed — see [EPIC-004 Closure](Engineering-Handbook/Releases/EPIC-004-Valuation-Intelligence-Closure.md) for the complete, accurate record of what was actually found and built across all eight of its sprints, not the single-Design-Study snapshot this section used to describe. Preserved here, corrected rather than deleted, per this engagement's "do not rewrite history" discipline — the original text's "an India-specific feasibility study should run next, in parallel with US-side implementation" framing was exactly the path the epic actually took (Sprint #002, then Sprint #003 implementing both markets together once the V1 set was confirmed), not a plan that was abandoned.

Epics 001, 002, 003, and 004 are all now formally closed (see [EPIC-001 Closure](Engineering-Handbook/EPICS/EPIC-001-Business-Quality-Intelligence-Closure.md), [EPIC-002 Closure](Engineering-Handbook/EPICS/EPIC-002-Financial-Strength-Intelligence-Closure.md), [EPIC-003 Closure](Engineering-Handbook/EPICS/EPIC-003-Growth-Intelligence-Closure.md), and [EPIC-004 Closure](Engineering-Handbook/Releases/EPIC-004-Valuation-Intelligence-Closure.md)). **Epic 005 may begin** — per EPIC-004 Closure's own evidence-based recommendation, conditioned on (not blocked by) a short, separately-scoped operational decision about activating Valuation Intelligence's currently-disabled-by-default kill switches, which can proceed in parallel with Epic 005's own first sprint rather than gating it.

**Epic 005 selection, made via a dedicated strategic decision sprint, not assumed**: a full evidence-based comparison of Risk Intelligence, Recommendation Consolidation, Macro/Market-Regime Intelligence, Portfolio Intelligence, and AI Research Analyst — see the [full decision document](Engineering-Handbook/Architecture/StockSense360-Next-Intelligence-Epic-Decision.md) — selected **Recommendation Consolidation Intelligence**. Rationale, in brief: it fills the most concretely evidenced gap (four validated engines exist with no synthesis layer reconciling them); carries the lowest cross-engine duplication risk (reads existing outputs only, never recomputes); requires no new provider data in either market (the lowest feasibility risk of any candidate); and its sole prerequisite — four stable, validated engines — is already satisfied today. Risk Intelligence was the leading alternative but was deferred (not rejected) because its most differentiating sub-areas (litigation, concentration, regulatory risk) depend on an unconfirmed NLP/free-text-extraction capability this codebase has never built, materially different from every prior engine's structured-numeric-field approach.

---

## Section 10 — Future Product Area: Mutual Fund Intelligence & Portfolio-Fit Support

**Epic 009 — Mutual Fund Intelligence & Portfolio-Fit Support**
**Status: Planned / Not Started — not started, not scheduled, not production-ready.**

This section documents the planned scope and philosophy for Epic 009, Mutual Fund Intelligence. It is recorded here to establish intent and design constraints, not to imply that any of this capability exists today. The platform currently covers equities only. No mutual fund analysis, screening, or recommendation logic has been built.

### Sequencing

Epic 009 is explicitly sequenced **after Portfolio and Watchlist Intelligence (Epic 007)**. Portfolio-context awareness — knowing what a user already holds — is a prerequisite for meaningful fund-fit analysis. Recommending a fund without knowing an investor's existing exposure, risk capacity, and goal horizon is the same error the platform already avoids for equity recommendations: opinion without context.

**Planning direction (subject to revision based on evidence, user needs, data-provider feasibility, and compliance requirements):**

| Priority | Area |
|---|---|
| Current | Daily Picks reliability and validation |
| Next | Daily Picks runtime and provider hardening |
| Future | Momentum Radar / Early Strength Scanner |
| Future | Portfolio and Watchlist Intelligence (Epic 007) |
| Future | **Mutual Fund Intelligence & Portfolio-Fit Support** |
| Future | Multi-broker read-only integrations |

This sequence is a planning direction, not a release commitment. It may be revised at any phase boundary based on evidence, data feasibility, regulatory considerations, or shifts in user need.

### Positioning

StockSense360 will not promote a fund simply because it delivered the highest recent return.

Mutual Fund Intelligence will evaluate quality, consistency, risk, cost, concentration, portfolio overlap, investor suitability, and goal alignment. Past return alone is not a recommendation. Every shortlisted fund must be explainable — what it does well, where its risks lie, who it suits, and when an investor should avoid or reconsider it.

No generic "best mutual funds" claim will be made. Suitability is individual and context-dependent.

### Planned Capabilities

**1. Fund Discovery and Screening**

- Category-wise screening: equity, debt, hybrid, index, ELSS, international, sectoral, and thematic funds where data coverage permits.
- Filters for investment horizon, risk level, expense ratio, AUM, and fund age.
- Direct Plan versus Regular Plan comparison where data is available.
- Data limitations shown honestly; coverage gaps named explicitly, not papered over.

**2. Fund Quality and Risk Analysis**

- Multi-period and multi-market-cycle return consistency — not a single trailing period.
- Benchmark-relative performance; alpha generated after accounting for benchmark and category beta.
- Risk-adjusted returns: Sharpe, Sortino, and similar measures where feasible.
- Volatility, maximum drawdown, downside risk, and recovery behaviour.
- Fund-manager tenure and strategy consistency; flagged if the manager who built the track record is no longer running the fund.
- Expense ratio evaluated as a structural drag on net return, not a footnote.
- Portfolio concentration (top-10 stock weight, sector weight) and evidence of style drift over time.

**3. Portfolio-Fit Analysis**

- Overlap detection between a fund's holdings and a user's direct-stock holdings.
- Overlap detection between multiple funds in the user's portfolio.
- Sector, market-cap, fund-house, and thematic concentration view across the combined equity exposure.
- Consolidated allocation view: equity, debt, gold, international, and cash.
- Duplicate-exposure warnings and diversification analysis — adding a new fund that replicates existing exposure is named as such.

**4. Goal-Based Investment Support**

- Suitability by investment horizon, risk capacity, and named goal.
- Goal categories: retirement, education, long-term wealth creation, emergency reserve, and income-oriented planning — each with appropriate fund-type guidance.
- SIP versus lump-sum decision-support; market-level context for lump-sum timing where relevant.
- Separation of short-term goals (where capital preservation dominates) from long-term equity investing (where return consistency matters more than short-term volatility).
- No automated investment execution; all fund actions remain the investor's own decision.

**5. Explainable Fund Summary**

Every shortlisted fund will explain:
- Why it is shortlisted — which specific quality, consistency, or fit criteria it satisfies.
- Key strengths — what the fund does well relative to category peers and benchmark.
- Key risks — what could go wrong; not buried, not minimised.
- Suitable investor profile — horizon, risk tolerance, and goal type this fund fits.
- Expected holding horizon — the minimum time period for which this fund's risk/return profile makes sense.
- Portfolio overlap impact — how this fund changes the user's existing exposure.
- When to avoid or reconsider — conditions under which this fund is unsuitable despite scoring well overall.

**6. Monitoring and Alerts**

- Fund-manager change alerts.
- Material strategy or style drift — identified from portfolio composition shifts, not just stated mandate.
- Sustained benchmark underperformance — named threshold, not a vague flag.
- Rising expense ratio — particularly if the Direct Plan gap is narrowing without justification.
- Excessive concentration in the fund's own holdings.
- Category and market-risk changes affecting the fund's risk profile.
- Portfolio overlap and rebalancing-review alerts when the user's combined exposure drifts.

### Philosophy Constraints

These constraints must hold regardless of what data sources become available:

- **No return chasing.** A fund's 1-year return will never be the primary or sole recommendation signal.
- **Capital preservation before return chasing.** For any goal horizon under three years, downside risk and consistency matter more than peak return.
- **Evidence before opinion.** Every fund assessment traces to named, inspectable metrics — not a proprietary black-box score.
- **Confidence is not certainty.** Past fund performance, however consistent, does not guarantee future returns. This will be stated clearly wherever fund summaries are shown.
- **Portfolio context matters.** A fund's suitability depends on what the investor already holds. Generic recommendations without portfolio context violate the platform's core design principle.
- **Human judgment remains in control.** No autonomous fund selection or investment execution. StockSense360 provides analysis; the investor makes the decision.
- **Data limitations shown honestly.** If a fund's full history is unavailable, if the benchmark data is incomplete, or if the manager tenure is unverifiable, those limitations are stated explicitly rather than silently omitted.

### Prerequisites and Open Feasibility Questions

Before this product area is scoped into an engineering epic, the following must be confirmed:

- Data provider availability for Indian mutual fund holdings, AUM, NAV history, expense ratios, and manager tenures at the required depth and update frequency.
- Data provider availability for US mutual fund and ETF equivalents (where applicable).
- Regulatory constraints on presenting fund analysis in a way that does not constitute regulated investment advice in either market.
- Whether the existing Data Fabric adapter pattern (provider adapter → resolution → engine adapter → pure engine) transfers cleanly to fund data, or whether a new ingestion architecture is needed.

None of these are assumed to be straightforward. This section records the intended product direction; the Design Study (when this area is prioritised) will confirm or adjust scope based on what is actually feasible.

---

## Section 11 — Epic 007 Sprint Plan: Portfolio Intelligence

**Status: Planned / Not Started.** This section documents the intended sprint-level requirements for Epic 007 — Portfolio Intelligence, ahead of any implementation. Nothing described here exists in the product today unless explicitly noted as already-shipped Portfolio Tracker or Paper Trading behavior (see `Documentation/STOCKSENSE_DOCUMENTATION.md` §17–18 for the current, already-live baseline). This is a documentation-only planning record, not an implementation commitment, timeline, or engineering estimate.

### Sprint Sequence

| Sprint | Name | Status |
|---|---|---|
| 001 | Portfolio Foundation | Planned / Not Started |
| 002 | Paper Trade Foundation and Daily Picks Sync | Planned / Not Started |
| 003 | Paper Trade Auto-Trigger, Simulated Execution and Notifications | Planned / Not Started |
| 004 | Post-Trade Analysis, Performance History and Learning Insights | Planned / Not Started |

---

### Sprint 001 — Portfolio Foundation

Covers: Holdings table, portfolio summary cards, Day P&L and Day Change %, currency normalization and data freshness, Edit Holdings usability and input-layout requirements.

#### Holdings Table

Intended column sequence:

```
Symbol | Quantity | Average Buy | Current | Invested | Value | Day P&L | Day Change % | P&L | P&L % | Signal | Actions
```

**Cumulative performance fields (existing concept, already used by Portfolio Tracker and Paper Trading today):**

- **P&L** — total unrealized gain or loss compared with the holding's average purchase price.
- **P&L %** — total unrealized gain or loss percentage compared with the holding's average purchase price.

**New daily performance fields:**

- **Day P&L** — monetary gain or loss for the user's actual holding quantity during the current trading day.
- **Day Change %** — percentage movement of the underlying security's current price compared with its prior official market close.

**Calculation principles:**

```
Day P&L      = Quantity × (Current Price − Previous Official Close)
Day Change % = ((Current Price − Previous Official Close) ÷ Previous Official Close) × 100
```

**Clarifications:**

- Day P&L reflects the user's actual holding quantity; Day Change % reflects the underlying security's daily price movement, independent of position size.
- Day P&L and Day Change % are current-trading-day metrics; P&L and P&L % are cumulative metrics since average purchase price. Users must be able to distinguish the two without ambiguity.
- Positive and negative values must remain semantically distinct as gains and losses.
- Missing market data must never be represented as a gain, loss, or zero movement.

#### Portfolio Summary Cards

Intended card sequence:

```
Holdings | Invested | Current Value | Day P&L | Day Change % | P&L
```

- **Holdings** — number of active holdings in the selected market or portfolio view.
- **Invested** — total original investment cost of included holdings.
- **Current Value** — latest calculated value of included holdings using the most recent valid market price.
- **Day P&L** — total monetary gain or loss for the included holdings during the current trading day.
- **Day Change %** — portfolio-level percentage movement during the current trading day.
- **P&L** — total cumulative unrealized profit or loss compared with investment cost.

**Calculation principles:**

```
Portfolio Day P&L      = Current Portfolio Value − Portfolio Value at Previous Official Close
Portfolio Day Change % = ((Current Portfolio Value − Portfolio Value at Previous Official Close)
                           ÷ Portfolio Value at Previous Official Close) × 100
```

**Clarifications:**

- Portfolio Day Change % must be calculated from portfolio values, never created by adding, averaging, or summing individual holdings' percentage changes.
- Daily portfolio cards must remain clearly separate from cumulative P&L.
- Daily metrics are informational portfolio-monitoring metrics only.

#### Market Data, Freshness and Multi-Currency Rules

Applies to both the Holdings table and the Portfolio summary cards:

1. Use the most recent valid market price and the prior official close for the relevant listing and exchange.
2. Preserve correct market and listing normalization: US holdings in USD, India holdings in INR, correct listing symbol, correct market suffix, correct market/exchange context where supported.
3. When a market is closed: show the latest completed trading-day movement, do not imply the price is live, and retain or expose a data-freshness timestamp where the product supports it.
4. When the current price, prior close, market, symbol, listing, currency, or exchange mapping is missing, stale, invalid, unavailable, or cannot be normalized safely: do not fabricate Day P&L or Day Change %, do not show zero as a substitute for unavailable data — show an unavailable/insufficient-data state instead.
5. Each holding's Day P&L must first be calculated in its native trading currency.
6. A market-specific view (e.g. US Holdings or India Holdings) should show daily values in that market's native currency.
7. A combined multi-market portfolio must not directly add USD and INR daily values.
8. Any cross-market aggregation must use the established reporting-currency and exchange-rate-normalization layer.
9. When converted portfolio figures are shown: the native-currency holding result remains unchanged, conversion changes only the reporting presentation, and daily performance in reporting currency may include foreign-exchange movement.

#### Edit Holdings — Numeric Input Usability

The Edit Holdings interface must support clear, comfortable, and accessible numeric entry for fields such as Quantity, Average Buy, Entry Price, Stop Loss, Target Price, and any other numeric portfolio or Paper Trade input.

Usability requirements:

1. Numeric values must not appear cramped against increment/decrement spinner controls.
2. Quantity inputs using increment/decrement arrows must provide a clearly visible internal gap or separated control area between the displayed number and the up/down spinner controls.
3. Arrow controls must not visually overlap, crowd, obscure, or touch the entered value.
4. The numeric value area and spinner-control area must remain visually distinct through appropriate spacing, padding, divider treatment, or a clearly separated control zone.
5. Users must be able to read multi-digit quantities easily without the spinner controls making the field appear compressed.
6. Quantity and Average Buy fields must have enough width for normal values, including decimals, without truncation, overlap, or visual crowding.
7. Numeric fields must align consistently across the Edit Holdings form.
8. The form must remain usable on desktop, tablet, and mobile widths.
9. Numeric controls must support keyboard input and accessible increment/decrement behavior.
10. Clear field labels, validation messages, and error states must remain visible without shifting or overlapping adjacent controls.
11. This is a usability and readability requirement only — it does not change any Portfolio calculation, validation rule, stock signal, Paper Trade trigger, or data model.

No specific pixel value, CSS framework, component library, or icon style is prescribed here — these are business-readable UX requirements so a future implementation can select the appropriate design-system solution.

---

### Sprint 002 — Paper Trade Foundation and Daily Picks Sync

Covers: create, edit, manage, and close simulated positions; Daily Picks-linked Paper Trade ideas; manual Paper Trade controls. This sprint's scope is a planning placeholder above and beyond the already-shipped Paper Trading module described in `Documentation/STOCKSENSE_DOCUMENTATION.md` §17 — no new requirements beyond the sprint name and sequencing are specified in this update.

---

### Sprint 003 — Paper Trade Auto-Trigger, Simulated Execution and Notifications

**Status: Planned / Not Started.**

A planned, paper-only capability allowing a user to define optional simulated-entry and exit conditions for a Paper Trade idea.

**Example user-defined conditions:**

- Simulated buy when price falls to or below a chosen entry price.
- Simulated buy when price rises above a chosen breakout price.
- Simulated close when a target price is reached.
- Simulated close when a stop-loss price is breached.
- Manually cancel an unfilled Paper Trade trigger.
- Pause or reactivate an existing Paper Trade trigger.

**A triggered action must:**

1. Create or update only a simulated Paper Trade position.
2. Never place a real broker order.
3. Never connect to a brokerage account.
4. Never execute or imply execution of a real investment transaction.
5. Clearly record the trigger price, observed market price, trigger time, simulated execution time, and source of the idea.
6. Preserve a durable audit history of trigger, cancel, pause, resume, and simulated-execution events.
7. Be protected against duplicate simulated execution when the same condition is checked more than once.
8. Respect market, symbol, and currency-normalization rules.
9. Allow manual user control at all times.
10. Clearly state that simulated fills may differ from real-market fills because of liquidity, slippage, brokerage fees, taxes, delays, market impact, and price movement.

**Optional informational notifications** (user opt-in/opt-out):

- Trigger armed
- Trigger condition met
- Simulated entry created
- Target reached
- Stop-loss reached
- Trigger cancelled
- Trigger paused
- Trigger resumed
- Data unavailable or stale, where a condition cannot be evaluated safely

Notifications are informational only — never framed as investment advice, real-order confirmation, or evidence of an executable live trade. This capability is a simulated-condition-monitoring feature, not trading automation: it never connects to a brokerage, never places a real order, and never manages real capital.

---

### Sprint 004 — Post-Trade Analysis, Performance History and Learning Insights

Covers: Paper Trade performance history, outcome analysis, learning insights, and a clear distinction between manual, Daily Picks-linked, and auto-triggered Paper Trades. This sprint's scope is a planning placeholder — no additional requirements beyond the sprint name and sequencing are specified in this update.

**Paper Trade daily-performance clarification** (applies wherever Paper Trade performance is documented or shown):

- Paper Trade positions should show daily simulated P&L separately from cumulative simulated P&L.
- Paper Trade summary views should preserve the same distinction used in the Holdings table and Portfolio summary cards: Day P&L, Day Change %, P&L, and P&L %.
- Paper Trade values remain simulated and must not imply real fills, achievable returns, live execution, brokerage charges, tax outcomes, liquidity, or slippage.
- This daily-performance clarification does not expand Sprint 003's Auto-Trigger functionality — it only clarifies how Paper Trade performance should be presented.

---

### Explicitly Out of Scope (Epic 007, all sprints documented in this section)

- Real brokerage account linking
- Real order placement
- Broker execution
- Automated real-money trading
- Trading automation
- Margin, leverage, futures, options, or derivatives execution
- Automatic capital allocation
- Portfolio rebalancing automation
- Tax calculation
- Guaranteed fills, prices, returns, or performance
- Intraday charting
- Advanced intraday performance attribution
- New market-data providers
- Changes to Daily Picks scoring, ranking, signal logic, or confidence
- Changes to Prediction Engine logic
- Changes to RCI logic
- Changes to existing Paper Trade auto-trigger logic beyond documenting this future scope
- Backend, API, frontend, database, workflow, or infrastructure implementation of any kind — this section is documentation only

---

## Section 12 — Platform-Wide Standard: User Local Time and Timestamp Display

**Status: Planned / Not Started.** This is a cross-cutting product standard, not scoped to any single Epic or feature area. It applies to every user-facing date, time, timestamp, schedule, alert, event, activity record, notification, generated result, simulated execution, validation result, and market-data freshness indicator across the platform — Daily Picks, Portfolio, Paper Trade, Watchlist, Alerts, and any future feature. Nothing in this section describes current behavior unless explicitly noted; it is a planning record for a documentation-only requirements review, not an implementation commitment or timeline.

### Core Product Principle

StockSense360 must show user-facing times in the individual user's own local timezone by default — a user in California sees Pacific Time, a user in the UAE sees Gulf Standard Time, a user in India sees India Standard Time, a user in Australia sees the applicable local Australian timezone, and a user travelling internationally sees their currently selected or browser-detected timezone unless they have manually selected a timezone preference. The platform must not force all users to interpret timestamps in IST, ET, UTC, or any single market timezone.

### Timestamp Storage and Data Contract Rules

1. Store all system timestamps as UTC instants.
2. APIs and persistence layers must preserve machine-readable timestamps using UTC or an unambiguous ISO-8601 timestamp with timezone offset.
3. Backend services must not pre-format user-facing timestamps in a fixed timezone such as IST, ET, GST, PST, EST, or EDT.
4. User-facing applications must convert timestamps for display only.
5. Timestamp conversion must not alter: source event time; market-session classification; exchange calendar logic; Daily Picks freshness logic; generation ownership; outcome-resolution logic; price-history interpretation; portfolio transaction ordering; audit-trail ordering.

### Default Timezone and User Control

1. Use the user's explicitly saved timezone preference when one exists.
2. Otherwise, use the browser or device timezone.
3. Otherwise, fall back safely to UTC.
4. Allow the user to manually select and change a timezone preference in settings.
5. Do not require GPS location, precise physical location, or personal location tracking merely to display local time.
6. Preserve the user's manually selected timezone preference across sessions when account settings support it.
7. Support both 12-hour and 24-hour time formatting according to the user's locale or explicit display preference where available.

### Daylight Saving Time Requirements

The system must use recognised IANA timezone identifiers and automatic daylight-saving handling — e.g. `America/Los_Angeles`, `America/New_York`, `Asia/Dubai`, `Asia/Kolkata`, `Australia/Sydney`. Do not rely on manually hard-coded UTC offsets for user-facing time display.

The platform must correctly handle: daylight-saving time changes; users travelling across timezones; dates that shift to the previous or next calendar day after conversion; historical timestamps displayed after daylight-saving changes; markets whose local exchange time differs from the user's personal timezone.

### Default User-Facing Display Pattern

Preferred visible pattern:

```
Updated: 01 Jul 2026, 06:55 PM GST · 4h ago
```

or:

```
Generated: 01 Jul 2026, 06:55 PM GST
```

The absolute local timestamp and the relative-age indicator must agree. Do not show only a relative value such as `4h ago` when a precise timestamp is material to investment interpretation.

### Market Time as Secondary Context

Market or exchange timezone may be shown only as secondary context when it helps explain: market open or close; Daily Picks generation schedule; prior official close; exchange holiday or weekend status; end-of-day portfolio values; price-alert trigger context; Paper Trade simulated execution conditions. Example:

```
Your time: 01 Jul 2026, 06:55 PM GST
Market time: 01 Jul 2026, 10:55 AM ET
```

The user's own local timezone remains the primary displayed time.

### Daily Picks Rules

1. The actual generation timestamp must come from the recorded `generated_at` event time and display in the user's local timezone.
2. The intended generation schedule must be displayed separately from the actual generation time.
3. Do not use a fixed sentence such as `Generated daily at 2:00 AM IST` as the primary visible timestamp for all users.
4. Instead, present concepts separately: **Last generated** (actual user-local timestamp), **Normal schedule** (converted user-local schedule), **Market reference time** (optional secondary context).
5. A scheduled event that occurs on a different calendar day after conversion must show the correct local day for that user.
6. Daily Picks freshness, `has_today`, market-day logic, scheduled trigger rules, holiday rules, and trading-session logic must remain based on the relevant market calendar and backend rules.
7. User-local timezone must never cause a Daily Picks run to be incorrectly treated as stale, fresh, missing, completed, or belonging to another market day.

### Portfolio, Alerts and Paper Trade Rules

The same standard applies to: Portfolio transaction timestamps; holdings refresh timestamps; Day P&L and portfolio-value timestamps; Watchlist updates; price alerts; Paper Trade trigger armed time; Paper Trade trigger condition time; simulated execution time; pause, resume, cancellation and close events; notifications; validation results; activity and audit logs.

Paper Trade and alert audit trails must preserve the original UTC event timestamp while displaying each event in the user's local timezone.

### Timezone Safety and Trust Rules

1. Never silently relabel a timestamp as local time without converting the original UTC instant correctly.
2. Never use the user's timezone to alter market prices, prior-close logic, trading-day calculations, daily return calculations, or historical event ordering.
3. Never infer a user's country, residence, tax status, market eligibility, or investment profile from timezone alone.
4. When timezone data is unavailable or invalid, show a safe fallback timezone such as UTC and make the fallback clear.
5. When a stored timestamp lacks timezone information and cannot be interpreted safely, do not present it as a precise local time without an explicit source-time assumption.
6. Timestamp presentation must remain explainable and auditable.

### Required Future Implementation Validation

Implementation must include deterministic coverage for: California user timezone display; UAE user timezone display; India user timezone display; Australian user timezone display; daylight-saving transitions; timestamps that move to a different local calendar day; user-selected timezone override; browser timezone fallback; UTC fallback; Daily Picks market freshness remaining independent of user timezone; correct ordering of audit-log and Paper Trade events; correct display of actual generation time separately from normal schedule time.

### Scope Boundary

This standard governs timestamp storage, conversion, display, settings preference, auditability, and test coverage. It does not by itself introduce: geolocation tracking; real-time location monitoring; broker integrations; trading automation; changes to Daily Picks scoring; changes to market-data providers; changes to Prediction Engine logic; changes to RCI; changes to Paper Trade trigger rules; changes to portfolio calculations; changes to market-calendar logic.

---

## Section 13 — Planned Cross-Cutting Strategic Initiatives

**Status: Planned / Not Started for both initiatives below.** These are cross-cutting strategic directions, not numbered Epics — they do not fit inside a single existing Epic's scope and are intentionally recorded here rather than assigned a new Epic number. Nothing in this section describes current, live, partially implemented, or validated behavior. Both initiatives depend materially on the existing Epic-numbered engines and on the current Daily Picks reliability/truthfulness work already underway; neither should be read as scheduled or committed to a timeline.

### Initiative A — Bear-Market Resilience & Market Regime Intelligence

**Status: Planned / Not Started.**

StockSense360 must not assume that it should produce fresh BUY calls every day. In weak, volatile, risk-off, or severe bear-market conditions, the system must be capable of becoming more selective, reducing exposure guidance, tightening eligibility criteria, or deliberately producing no new BUY calls.

**Purpose.** This initiative is intended to improve capital preservation, investor trust, market-context awareness, and explainability across: short-term Daily Picks; medium-term Daily Picks; the current strategic 3–6 month Daily Picks horizon; future true long-term investment intelligence (Initiative B, below); portfolio risk awareness; and Paper Trade decision support, without real execution.

**Market Posture Framework (planned states).**

- Risk-On
- Neutral
- Cautious
- Risk-Off
- Severe Risk-Off

These are market-environment classifications, not promises of future returns.

**Decision Rules (planned).** A severe market-risk condition must be able to override an otherwise attractive individual-stock opportunity — a strong individual stock score must not automatically override severe market-risk conditions. Valid outcomes may include: normal opportunity search; fewer and more selective candidates; higher minimum quality and relative-strength requirements; lower confidence and more conservative allocation guidance; defensive watchlist/research candidates only; no fresh BUY calls today; existing-position risk review prompts. **"No new BUY calls" is a deliberate risk-control outcome, not a data failure.**

**Inputs to evaluate (planned, not all confirmed to exist or be reliable today).** The future implementation must evaluate and validate relevant independent inputs, including: broad-index trend versus medium- and long-term moving averages; drawdown from recent highs; market breadth and participation; volatility level and volatility trend; sector leadership and defensive rotation; relative strength of quality stocks; global risk conditions relevant to India and the US; rates, currency, crude oil, and macro stress where materially relevant; market-specific data availability and limitations. No claim is made that every input already exists or is reliable today — that is precisely what the planned validation (below) must establish.

**Explainability (planned user-facing output concept).**

```
Market Risk Gate: CAUTIOUS
Why: market trend, breadth, volatility, sector leadership, and relevant macro conditions.
Daily Picks behaviour: stricter eligibility and entry requirements.
```

And, for a severe-risk state:

```
No fresh BUY calls today.
This is a deliberate capital-preservation outcome, not a data or system failure.
```

**Validation required before any marketing claim.** StockSense360 must not claim bear-market reliability, downside protection, or defensive outperformance without regime-based, out-of-sample validation. Planned validation must cover both India and US markets, including: bear markets; major corrections; volatility spikes; rate/inflation shocks; sector-led sell-offs; recovery phases; and different market regimes generally. Required metrics include: BUY-signal frequency by regime; abstention/no-new-buy frequency; hit rate and average return by regime; maximum drawdown versus relevant benchmark; false-positive BUY rate; entry-zone and stop-loss behaviour; relative strength versus market; the effect of realistic delay, slippage, gaps, and transaction costs; and stability across India and US. The goal is not to predict every market move or promise accuracy — the goal is to become more defensive as market risk rises and to avoid low-quality entries.

**Roadmap position.** This initiative is dependent on: current Daily Picks production reliability work; Daily Picks truthfulness corrections (the shared error-safety and user-local-time correction work already scoped in this session's audits); the existing quality, financial-strength, growth, valuation, and recommendation layers (Epics 001–006); and future Portfolio Intelligence (Epic 007) where portfolio-aware risk guidance is involved. It should be designed before any claim is made that Daily Picks is reliable in bearish markets.

---

### Initiative B — Long-Term Investment Intelligence

**Status: Planned / Not Started.**

A future capability for genuine long-term investing research and portfolio-aware decision support over approximately 3–5+ years. This is separate from the current Daily Picks "Long Term" horizon: **current Daily Picks "Long Term" refers to a strategic approximately 3–6 month horizon. It must not be represented as a 3–5+ year investment recommendation engine.**

**Core principle.** A true long-term system must answer two separate questions: (1) is this business worth owning over several years? (2) is this the right valuation, timing, and portfolio context to begin or add to a position? These two questions must not be collapsed into one generic BUY/HOLD/SELL label.

**Decision framework (planned, two layers).**

*Investment Merit:* business quality; financial strength; cash-flow durability; long-term growth durability; competitive position where evidence is available; governance and capital allocation; valuation and margin of safety.

*Entry Approach:* current valuation opportunity; broad market regime; sector conditions; entry-risk assessment; staged accumulation logic; portfolio concentration and diversification context.

A company can have strong long-term investment merit while its immediate entry approach is "wait," "watch," or "accumulate gradually."

**Future user-facing states (planned research states, not current live signals).**

- High-Quality Investment Candidate
- Accumulate Gradually
- Watch — Valuation Too High
- Wait for Better Entry Conditions
- Thesis Under Review
- Avoid — Fundamental Risk

None of these are guaranteed outcomes, personalised financial advice, or automated execution instructions.

**Capability scope (future work areas).** Multi-year business-quality assessment; ROCE/ROE consistency; margin durability; free-cash-flow quality; debt, liquidity, and interest-coverage resilience; sales, earnings, and cash-flow growth quality; cyclicality and responsible reinvestment; valuation using more than PE alone; sector-aware valuation context; governance and ownership checks where data is sufficiently reliable; dilution, pledge, capital-allocation, and related-risk monitoring where available; thesis-monitoring alerts for earnings, debt, margins, governance, and competitive deterioration; portfolio fit, sector concentration, existing-holding overlap, country/currency exposure, and diversification context; staged-entry and staged-addition guidance; and a clear separation between research support, simulated Paper Trade, and real broker execution. **StockSense360 must not place real trades, connect to brokers for execution, or present long-term research states as guaranteed investment outcomes.**

**Validation required before any long-term claim.** No claim of long-term investment reliability may be made without robust, rolling, out-of-sample validation. Planned validation should consider: 3-, 5-, and (where data permits) 7-year periods; bull, sideways, correction, and bear-market regimes; sector cycles; survivorship-bias controls where feasible; delisted or materially impaired companies where data permits; India and US market differences; valuation starting-point effects; concentration and diversification outcomes; and realistic delay, data-availability, and transaction-cost assumptions. The goal is not to promise multibaggers or guaranteed returns — the goal is to help investors assess durable businesses, valuation discipline, portfolio fit, and thesis deterioration more responsibly.

**Roadmap position.** This planned initiative depends materially on: Epic 001 — Business Quality Intelligence; Epic 002 — Financial Strength Intelligence; Epic 003 — Growth Intelligence; Epic 004 — Valuation Intelligence; Epic 007 — Portfolio Intelligence; and later work on cross-market data quality, governance evidence, portfolio fit, and validation infrastructure. It should be sequenced after the current Daily Picks reliability/truthfulness programme and in coordination with Portfolio Intelligence, not treated as a simple extension of daily signals.

---

### Planned Cross-Cutting Initiative — Horizon-Aware Capital Protection, Trailing Risk Controls & Thesis Monitoring

**Status: Planned / Not Started.**

**Core principle.** A single generic stop-loss rule must not be applied equally to short-term trades, medium-term positions, strategic 3–6 month holdings, and genuine multi-year investments. Protection logic must adapt to: intended holding horizon; market volatility; market regime; entry conditions; business quality; investment thesis; portfolio concentration; and user-defined risk tolerance where supported in the future. This is planned decision-support and monitoring functionality only. It must never be described as guaranteed protection, guaranteed loss prevention, automatic real trade execution, personalised financial advice, or a substitute for investor judgment.

#### Horizon Framework (planned)

**1. Short-Term Horizon — approximately 1–5 days.** Planned purpose: protect capital quickly; define an initial invalidation level before entry; preserve gains when price moves favourably. Planned mechanisms: initial fixed risk level/stop-loss reference; optional trailing protection after a clearly defined favourable move; volatility-aware trailing method where validated; entry-zone invalidation; market-regime-aware tightening during elevated risk; clear trigger reason and timestamp. Any future trailing mechanism must be explainable, including: original risk level; activation condition; high-water mark or equivalent reference; revised protection level; trigger reason; and whether the indication is intraday, end-of-day, or end-of-week based.

**2. Medium-Term Horizon — approximately 2–4 weeks.** Planned purpose: protect against a material trend reversal while allowing normal volatility. Planned mechanisms: initial downside-risk level; volatility-aware trailing protection; higher-timeframe trend confirmation; stricter risk controls during Cautious, Risk-Off, or Severe Risk-Off regimes; end-of-day or end-of-week review logic where appropriate; a clear distinction between a warning, a review signal, and an invalidation condition. Medium-term protection should not be so tight that normal market noise repeatedly forces exits.

**3. Current Daily Picks "Long Term" Horizon — approximately 3–6 months.** The existing Daily Picks "Long Term" horizon is a strategic approximately 3–6 month horizon. It is not the same as a 3–5+ year investment horizon. Planned protection approach: a Capital Protection Level rather than a simplistic daily trailing stop; the protection level may rise after sustained favourable movement; broader volatility allowance than short- or medium-term positions; market-regime and sector-risk consideration; the default trigger should normally prompt a structured review, not automatically imply an immediate exit; use higher-timeframe confirmation where appropriate; distinguish price deterioration from fundamental thesis deterioration. Intended future user-facing concept:

```
Initial Risk Level
Capital Protection Level
Protection Status
Reason for Latest Adjustment
Review Trigger
```

**4. Genuine Long-Term Investment Horizon — approximately 3–5+ years.** Genuine long-term investing requires Investment Thesis Monitoring first, with price-based protection as a secondary optional alert. The core question must be: has the business or investment thesis deteriorated materially? Planned thesis-monitoring inputs may include: sales, earnings, and cash-flow deterioration; margin durability; debt and liquidity stress; interest coverage; capital allocation; dilution; governance or ownership warning signs; valuation excess; competitive-position deterioration where evidence is available; sector and macro changes where materially relevant; portfolio concentration and exposure risk. Planned user-facing research states may include: Thesis Intact; Review Required; Reduce Exposure Consideration; Thesis Under Review; Thesis Invalidated; Avoid — Fundamental Risk. **A price decline alone must not automatically close a genuine long-term investment position.** A price-based protection alert may prompt review, but an exit conclusion must consider business quality, financial strength, valuation, market conditions, and the documented investment thesis.

#### User Controls and Transparency (planned)

Future user controls: whether a protection alert is enabled; conservative/balanced/wider risk tolerance selection only after validated design; pause, resume, adjust, or cancel Paper Trade protection rules; no real broker execution; no automatic real sell order; all changes logged and auditable.

For every future protection adjustment or trigger, record: original entry/reference price; original risk level; current protection level; high-water mark or relevant reference; adjustment timestamp; price and market context; market-regime state; reason code; whether triggered by price, volatility, technical trend, or thesis event; resulting suggested action; and user response, where relevant.

#### Paper Trade Boundary (required)

Automated monitoring and simulated execution may be considered only within Paper Trade. All Paper Trade actions remain simulated — no brokerage integration or real order execution. User notifications must remain opt-in. Duplicate-trigger protection, durable audit history, pause/resume/cancel capability, and notification controls are required before any automated Paper Trade protection workflow is considered complete.

#### Bear-Market Integration (dependency)

This initiative depends on Bear-Market Resilience & Market Regime Intelligence (above). During Cautious, Risk-Off, or Severe Risk-Off regimes, the future system may: tighten protection requirements; reduce new-position eligibility; lower confidence or allocation guidance; require higher quality and relative strength; issue review prompts; avoid new BUY calls entirely. **Market regime must not silently override a protection recommendation. Any market-regime effect must be shown to the user with an explanation.**

#### Validation Required Before Any Product Claim

StockSense360 must not claim trailing-stop effectiveness, capital protection, downside protection, or improved risk-adjusted outcomes without robust out-of-sample validation. Future validation must cover both India and US markets, including: bull markets; sideways markets; corrections; bear markets; volatility spikes; sector sell-offs; gap-down scenarios; recovery phases; different liquidity conditions. Required measurements: maximum drawdown; avoided-loss rate; premature-exit rate; missed-upside rate; stop/alert trigger frequency; false trigger rate; outcome after trigger; comparison against fixed-stop and no-protection baselines; realistic delay, slippage, gap, and transaction-cost assumptions; stability by market, horizon, sector, and regime. The goal is disciplined risk management and explainable decision support, not perfect exits or guaranteed returns.

#### Dependencies and Sequencing

This initiative depends on: current Daily Picks production reliability and truthfulness work; the shared safe-error and user-local timestamp work; Bear-Market Resilience & Market Regime Intelligence; Long-Term Investment Intelligence; Epic 007 — Portfolio Intelligence; the Paper Trade foundation and later Paper Trade workflow capabilities (Section 11); and reliable historical price, volatility, market-regime, and outcome-validation data. It must be designed and validated before it is presented as an investor-protection feature.
