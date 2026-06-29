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
| **004** *(proposed numbering)* | Valuation Intelligence | **In Progress — Integrated into Prediction Engine, ready for Daily Picks Validation** | Epics 002 and 003 (both now closed) | Sector-relative/growth-adjusted valuation replaces the current static P/E cutoff, as an explainable engine. | Sprints #001–#007 all complete. [Sprint #007](Engineering-Handbook/Releases/Sprint-007-Valuation-Intelligence-Prediction-Engine-Integration.md) implemented Sprint #006's design exactly — `_apply_valuation_intelligence_adjustment()` wired as the last confidence-pipeline step, asymmetric +2 (gated)/-4 (ungated) cap, all-clear cross-engine gate, both markets, kill switches defaulting to **disabled**. **Live re-validation against 372 real companies confirms the gate works today**: `RELINFRA`/`VEDL` (Sprint #005's worst value traps) both show Growth Intelligence grading "avoid" right now and are confirmed blocked live; gate hit-rate 35-46%. Real incremental cost confirmed **near-zero** (shares already-fetched data). 754/754 full suite passing, zero crashes. **Recommendation: Ready for Daily Picks Validation** (Sprint #008). |
| **005** *(proposed numbering)* | Risk Intelligence | **Planned** | Epics 002–004 (consolidates risk signals those engines surface) | A single Risk Intelligence engine replaces today's scattered regime/risk-reward/penalty logic. | Not started — no design study yet. |
| **006** *(proposed numbering)* | Recommendation Intelligence Consolidation | **Future** | Epics 002–005 | The existing Prediction Engine evolves to consume Business Quality, Financial Strength, Growth, Valuation, and Risk as first-class engine inputs, rather than its current internal ad hoc factor blend. | Not started — explicitly sequenced after the component engines exist, per the same "validate the parts before integrating" discipline used throughout Epic 001. |
| **007** *(proposed numbering)* | Portfolio Intelligence | **Future** | Epic 006 (needs a stable, consolidated recommendation signal to be portfolio-aware about) | Concentration-risk and correlation-aware recommendations against a user's actual holdings — the "Portfolio Copilot" capability, confirmed not to exist today. | Not started — requires product scoping before any engineering, per the existing engineering roadmap's own assessment. |
| **008** *(proposed numbering)* | AI Research Analyst | **Future** | Epic 006/007 (needs richer, consolidated explainability data to be grounded in) | A true conversational analyst layer, distinct from today's rule-based Explainability Layer. | Not started — deliberately last; explicitly gated on richer explainability data existing first. |
| — | Daily Picks Intelligence | **Completed** (as a product feature; not a separate epic) | Recommendation Intelligence (Epic 006 will indirectly improve it) | N/A — already shipped. | No dedicated epic planned; improves automatically as upstream engines mature. |

---

## Section 4 — Current Status

**Completed:**
- Epic 001 — Business Quality Intelligence, both markets, closed.
- Epic 002 — Financial Strength Intelligence, US (non-FINANCIAL, non-REAL_ESTATE) market, closed — see [EPIC-002 Closure](Engineering-Handbook/EPICS/EPIC-002-Financial-Strength-Intelligence-Closure.md).
- **Epic 003 — Growth Intelligence, India confidence-only + US explainability-only, closed** — see [EPIC-003 Closure](Engineering-Handbook/EPICS/EPIC-003-Growth-Intelligence-Closure.md). Live in `prediction_engine.py` for India; empirically confirmed not to affect Daily Picks ranking in either market (339-company validation); US numeric integration explicitly withheld pending a future outcome-correlation re-measurement, named as accepted technical debt, not a gap.
- Recommendation Intelligence (Prediction Engine + Ranking & Filtering) and Daily Picks, as pre-existing, mature platform capabilities — not products of the Epic-numbered engine architecture, but real and operational.

**In Progress:**
- **Epic 004 — Valuation Intelligence, Sprints #001–#007 complete.** Sprints #001–#006 (Design Study through Integration Readiness Decision) established a real, load-bearing constraint — financially distressed companies with degenerate multiples score near the engine's maximum (`RELINFRA` 73/100 → a real, measured **-82.0%** return) — and designed an asymmetric +2 (gated)/-4 (ungated) confidence cap with a cross-engine safeguard gate to address it. **[Sprint #007](Engineering-Handbook/Releases/Sprint-007-Valuation-Intelligence-Prediction-Engine-Integration.md) implements that design exactly** in `prediction_engine.py` — `_apply_valuation_intelligence_adjustment()` as the last confidence-pipeline step, an all-clear (AND) cross-engine gate (a documented, conservative refinement of Sprint #006's own OR-phrased text), both markets, kill switches defaulting to **disabled**. **Live re-validation against 372 real companies confirms the gate works today, not just historically**: `RELINFRA` and `VEDL` — Sprint #005's two worst value traps — both show Growth Intelligence grading "avoid" right now and are confirmed blocked live; gate hit-rate measured at 35.4% (India)/45.7% (US) of otherwise-eligible boosts. Double-counting reviewed for all three named pairs — no material overlap found. Real incremental latency confirmed **near-zero** (US reads already-fetched data; India's screener fetch shares Growth Intelligence's own cache). 59 new tests, 754/754 full suite passing, zero crashes. **Recommendation: Ready for Daily Picks Validation** (Sprint #008).

**Next Up:**
- A Daily Picks Validation sprint (mirroring Epic 003 Sprint #008) — confirm Daily Picks consumes Valuation Intelligence's confidence-only contribution exactly as intended (eligibility-floor effects only, never ranking), empirically, not just structurally — the same standard Growth Intelligence's own equivalent sprint already met.
- Sector-relative percentile's own feasibility question (does a sector-benchmark/peer-aggregation data source exist or need building?) — separate from this epic's already-confirmed raw-ratio availability.
- Lower priority, can run independently of Epic 004: Growth Intelligence's accepted technical debt — re-measure the US outcome correlation once enough calendar time has passed for non-clustered anchor windows (most current US anchors share a Dec-2025 fiscal year-end); corporate-action handling for Reinvestment Efficiency's invested-capital calculation; the BANDHANBNK-style scraper gap; a full-sample Margin Trend re-validation.
- Reuse the now-four-times-proven Data Fabric pattern (provider adapter → resolution → engine adapter → pure engine) rather than re-deriving an architecture a fifth time — SSDS-008 already confirms this pattern transfers without modification.

**Future:**
- Epics 005–008 (Risk, Recommendation Consolidation, Portfolio, AI Research Analyst) as proposed, unstarted, with no design study yet for any of them.

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

## Recommendation on Epic 004

**Superseded note:** this section originally tracked Epic 003's own recommendation while that epic was still in progress (its Design Study had just completed, before the India Feasibility Study resolved its data-availability question). Epic 003 is now formally closed — see [EPIC-003 Closure](Engineering-Handbook/EPICS/EPIC-003-Growth-Intelligence-Closure.md) for the complete, accurate record of what was actually found and built across all eight of its sprints, not the single-Design-Study snapshot this section used to describe. Preserved here, corrected rather than deleted, per this engagement's "do not rewrite history" discipline — the original text's "roughly half the India-side metric catalogue is honestly unconfirmed" framing was a defensible, evidence-bounded estimate at the time it was written, later revised by the very feasibility study it called for.

Epics 001, 002, and 003 are all now formally closed (see [EPIC-001 Closure](Engineering-Handbook/EPICS/EPIC-001-Business-Quality-Intelligence-Closure.md), [EPIC-002 Closure](Engineering-Handbook/EPICS/EPIC-002-Financial-Strength-Intelligence-Closure.md), and [EPIC-003 Closure](Engineering-Handbook/EPICS/EPIC-003-Growth-Intelligence-Closure.md)). Epic 004 — Valuation Intelligence — has completed its own Design Study ([SSDS-008](Engineering-Handbook/SSDS/SSDS-008-StockSense360-Valuation-Intelligence-Engine.md)), mirroring the now-three-times-proven Design-Study-then-Feasibility-Study-then-Implementation sequence exactly. Per that document's own Final Recommendation, full-scope implementation is **not yet recommended** — an India-specific feasibility study (mirroring Epic 003 Sprint #002) should run next, in parallel with US-side implementation of the highest-confidence metric subset, not a leap to full, both-market, all-philosophy implementation.
