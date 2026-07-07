# Multibagger Page — Forensic & Scientific Audit

## 0. Status and Purpose

**Status: Audit complete for the current, live Multibagger screens. Section 10 specifies future explainability capabilities that do not exist yet.** No production code was modified to produce this audit — every finding below is from direct source-code inspection (full read of `frontend/src/app/multibagger/page.tsx`, `backend/api/routers/multibagger.py`, `backend/services/multibagger_scorecard.py`, `backend/services/fundamentals_cache.py`'s three screen SQL definitions, and `backend/services/thresholds.py`). Nothing in Section 10 is implemented; it is a functional specification for future work, following this codebase's established documentation-before-implementation discipline (see the [Portfolio](Portfolio-Page-Forensic-Performance-Audit.md) and [Daily Picks](Daily-Picks-Page-Forensic-Performance-Audit.md) audits, the [Stock Movement Explanation Engine spec](Research-Analyst-Stock-Movement-Explanation-Spec.md), and [EPIC-008](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md) for precedent).

**Scope:** the three live screens — Quality Compounders, Multibagger Discovery, 10-Bagger Early Detection (`GET /api/multibagger/screen`) — their SQL filter definitions (`_SCREENS` in `fundamentals_cache.py`), the rule-based scorecard/verdict layer (`multibagger_scorecard.py`), and the frontend at `/multibagger`.

## 1. What Works Well

**Engineering:**
- **Zero N+1 pattern — a real contrast with Portfolio/Daily Picks.** Screen results come entirely from a nightly-refreshed Postgres cache (`stock_fundamentals_cache`); there is no per-row live quote/prediction fetch anywhere in `page.tsx`. This is the most efficiently-built of the three pages audited so far this session.
- **`staleTime: 60min` + `keepPreviousData`** (`page.tsx:74-83`) is exactly proportionate to a nightly-batch data source — no over-fetching, no header-jump on market/screen toggle (explicitly reused from the Daily Picks IN/US fix per its own comment).
- **Errors never leak internals.** `get_screen` (`multibagger.py:52-64`) catches all exceptions and returns a generic message, logging the real exception server-side only — correctly distinguishes a genuine zero-result screen (`status: "ok", count: 0`) from a computation failure (`status: "unavailable"`).
- **Type-safety boundary is explicit and documented.** `_num()` (`multibagger_scorecard.py:24-40`) exists specifically because Postgres `NUMERIC` returns `Decimal`, and arithmetic (not comparison) between `Decimal` and `float` raises — a real bug class caught and fixed, with a regression test (`test_multibagger_decimal_handling.py`).
- **Never-fabricate discipline extends to the checklist's own documentation.** The module docstring explicitly discloses that "trend" checks (debt rising, pledge rising) are implemented as latest-snapshot checks instead, "labelled as such... NOT claiming a trend we can't see."
- **Order Book/Revenue > 3x was dropped, not faked.** The `elite_strong_buy` comment explicitly states this original-formula metric was dropped because no scraped source has it for any stock — "faking it would be worse than omitting it."

**Investment philosophy:**
- **The three-screen structure is methodologically sound and matches real practitioner frameworks** — Quality Compounders (Buffett/Munger-style moat+ROE+low-debt, long holding period), Multibagger Discovery (looser growth-stage screen, a GARP/small-cap-growth lens), 10-Bagger Early Detection (a deliberately messier, higher-risk, pre-inflection screen) — this strict→looser→speculative progression mirrors how quality-growth investing literature structures a multi-tier watchlist (cf. Lynch's "ten-bagger" framing, Terry Smith/Fundsmith-style quality-compounding criteria).
- **The Anti-Loss red-flag override is a real risk-management primitive**, not just a scoring gimmick — a verdict downgrade a raw percentage-score threshold alone can't produce, and explicitly a hard ceiling that promotion logic cannot override (`multibagger_scorecard.py:135, 143, 161`).
- **Thresholds are centralized in `thresholds.py`**, not hardcoded per-screen — consistent with this codebase's SES-002 threshold-registry convention.

## 2. What Is Partially Working

- **Two different growth-window definitions for "quality" are used without cross-reference.** The SQL screen (`fundamentals_cache.py:245-246`) gates IN quality-compounder eligibility on **5Y** sales/profit growth `> 10%`; the scorecard checklist (`multibagger_scorecard.py:65-66`) separately scores **3Y** growth `> 12%` as one of 10 checks. Defensible as "eligibility gate vs. ranking signal," but currently undocumented as such anywhere a reader would see it.
- **`elite_strong_buy`'s growth bar (10%) is nominally lower than the base checklist's own 3Y growth check (12%).** Not a live bug (elite only promotes an already-passing verdict), but reads as an inconsistency on inspection.
- **US quality-compounder screen substitutes 3Y for 5Y growth** (`fundamentals_cache.py:253-270`, an honest, disclosed substitution due to yfinance's 4-year cap) — meaning the same screen name enforces a materially different, looser growth-persistence bar in the US than in India, uncommunicated to the user on the page itself.

## 3. What Is Scientifically Weak

- **Point-in-time snapshot data, presented via language that implies trend.** Checklist labels ("ROE, not visibly declining vs 5Y avg") approximate genuine multi-year trend analysis using only the latest value plus one historical average field — quality-investing methodology (Buffett's emphasis on *consistency* of ROE) calls for a real multi-year series. Already disclosed in code comments, but remains a real scientific weakness against the "quality compounder" thesis.
- **No look-ahead/survivorship-bias-controlled backtest is evident for any screen.** These are point-in-time hard filters — no evidence of a walk-forward backtest of the multibagger screens themselves (distinct from Daily Picks' own separately-validated backtest, a different engine and methodology entirely).
- **P/E and EV/EBITDA absolute caps are static, sector-agnostic valuation ceilings.** Damodaran-style sector-relative multiple methodology (already the stated approach in this codebase's own Valuation Intelligence Engine, SSDS-008) treats absolute P/E ceilings as a blunt instrument — a 35x P/E software company and a 35x P/E cyclical industrial are not equivalently "cheap."
- **No reconciliation with the Business Quality Engine's own validated hard-gate rejection logic for IN stocks.** `multibagger_scorecard.py:108-109` explicitly states `business_quality_score` is "Always None for IN today" — the one piece of additional, independently-validated distress-detection evidence this codebase has contributes zero signal to India Multibagger scoring, even though the India Business Quality Adapter is confirmed live elsewhere (Sprint #007 — India Business Quality Adapter).

## 4. What Is Missing

- Historical validation / outcome tracking for the Multibagger screens themselves (Section 6).
- Sector-relative scoring — no sector-adjusted thresholds anywhere in the three screens.
- Explainability at the screen level (Section 10).
- Portfolio integration — no cross-reference between a screen result and existing holdings.
- Alerting — no notification when a stock enters/exits a screen between refresh cycles.
- Confidence/uncertainty quantification — the score is a simple pass-count fraction; two stocks scoring 8/10 with different checks failed are treated identically.

## 5. What Should Be Redesigned

- **Growth-window inconsistency (Section 2)** should be unified or explicitly documented as an intentional two-lens design.
- **Absolute valuation caps should move toward sector-relative bands**, reusing the Valuation Intelligence Engine's already-solved methodology rather than maintaining a second, cruder valuation gate in parallel.
- **India Business Quality integration should be wired in** — the fastest, lowest-risk scientific-rigor improvement available (an existing, already-built distress-detection signal, currently a structural no-op for IN).
- **The Anti-Loss red-flag system should evolve toward genuine trend detection**, once multi-year fundamentals history is actually cached — the current implementation is honest about this gap, not silently wrong, but it is the single most consequential scientific upgrade available if multi-year data becomes available.

## 6. Historical Validation Gap

**Confirmed absent.** Unlike Daily Picks (a dedicated `/api/validation/results` walk-forward backtest endpoint and page section) and unlike Growth/Valuation Intelligence (each with a dedicated Outcome Validation sprint in this codebase's history), **no equivalent validation artifact exists for any of the three Multibagger screens.** This is the single largest scientific-validity gap identified: every threshold in `_SCREENS` and `multibagger_scorecard.py` is derived from investment-philosophy first principles and honestly-disclosed data-availability constraints, but has not been empirically outcome-tested against this platform's own historical data the way every other scoring engine in this codebase has been.

## 7. Academic and Professional Investing Alignment

| Screen/Rule | Alignment | Note |
|---|---|---|
| Quality Compounders (ROE>18%, ROCE>15%, D/E<50%, low pledge) | Strong — matches Quality (QMJ) factor literature and classic Buffett/Munger criteria | Snapshot-vs-trend limitation (Section 3) |
| Multibagger Discovery (growth 15%+, looser D/E, mid/small-cap band) | Reasonable — matches GARP/small-cap growth screening precedent (Lynch's "ten-bagger" search space) | No sector adjustment |
| 10-Bagger Early Detection (growth 20%+, OPM>8%, ICR>2, looser valuation) | Directionally reasonable as a speculative/turnaround screen, but the widest gap from rigorous academic factor discipline — high-growth-at-any-valuation screens are exactly the population academic momentum/growth-trap research warns is most prone to false positives (the same value-trap risk already documented for this codebase's own Valuation Intelligence Engine) | No outcome validation exists |
| Absolute valuation caps (P/E<35/50/60, EV/EBITDA<20) | Weak vs. sector-relative practice (Damodaran) | Section 3 |
| Anti-Loss red flags | Reasonable risk-control primitive, methodologically sound in spirit | Snapshot proxy limitation, honestly disclosed |

## 8. Priority-Ranked Improvement Plan

1. **High** — wire the already-validated India Business Quality Adapter's hard-gate output into the IN scorecard (currently a structural no-op, fastest available rigor win).
2. **High** — commission a Multibagger-screen Historical/Outcome Validation study, mirroring the existing Growth/Valuation Intelligence Outcome Validation methodology — the single largest scientific-credibility gap.
3. **Medium** — move valuation gates from absolute caps toward sector-relative bands, reusing Valuation Intelligence Engine's existing methodology.
4. **Medium** — document (or unify) the 3Y-vs-5Y growth-window relationship between the SQL screen and the scorecard checklist.
5. **Low** — add Portfolio-holdings cross-reference and a "why this stock, not a peer" explainability surface (Section 10), once the Intelligence Engine/Research Analyst layers mature.
6. **Low** — add screen entry/exit change alerts — a straightforward extension of the existing nightly refresh + cache pattern.

No item above is authorized or scoped by this document — each requires its own separate implementation-sprint approval.

## 9. Integration Opportunities

- **Intelligence Engine:** the scorecard's confirmed/red-flag tiering is conceptually adjacent to the Intelligence Engine's own tiered-evidence gates (Instrument Type/Tradability/Liquidity/Data Confidence, `backend/services/intelligence_engine/`) — a future refactor could have Multibagger consume the Intelligence Engine's Data Confidence score directly, rather than maintaining a separate, parallel notion of data completeness.
- **Research Analyst (Epic 008):** a natural, direct consumer — "why did this stock pass Quality Compounders, and what would make it fail" is exactly the Research Answer Contract shape ([EPIC-008 §9](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#9-research-answer-contract)) already specified for stock-level conversations. Must consume the scorecard's existing checklist output as evidence, never recompute an equivalent score independently (the same "consume validated evidence" rule EPIC-008 §3 already establishes).
- **Portfolio Copilot:** "which of my holdings are Quality Compounders" and "suggest a Multibagger Discovery candidate I don't already hold" are direct, low-risk future Portfolio Copilot capabilities ([Portfolio Copilot Vision §11](Portfolio-Page-Forensic-Performance-Audit.md#11-portfolio-copilot-vision)) once that layer exists — "Better Investment Alternatives" in that vision document is the natural home for a Multibagger-screen cross-reference.

## 10. Future Capability — Multibagger Explainability

**Status: Planned / Not Started. Documentation only — no implementation, scoring, or UI has been built.** Directly addresses Section 4's confirmed explainability gap. Every item below must consume already-computed evidence rather than introduce a second, parallel scoring mechanism — the same architectural discipline already established for the Stock Movement Explanation Engine ("not a standalone hardcoded widget") and the Portfolio Copilot vision's "no duplicate scoring engines" rule.

- **Why this qualifies.** Render the scorecard's own `checks[]` array (already computed, `multibagger_scorecard.py:59-82`) as a labeled pass/fail explanation per stock, rather than (or in addition to) a bare score — the data already exists in the API response; this is a display gap, not a data gap.
- **Why this did not qualify.** For a stock that fails the *screen* (SQL filter) entirely, it never reaches the scorecard at all today — there is no mechanism to show a near-miss stock why it was excluded (e.g., "ROCE 14.2%, needed >15%"). This would require running the scorecard's checklist logic against the full universe, not just the post-filter survivors — a genuinely new capability, not a display change.
- **Screen entry/exit changes.** Requires persisting each nightly refresh's screen membership and diffing against the prior run — the same daily-snapshot architectural need already identified for [Daily Picks' Yesterday Comparison](Daily-Picks-Page-Forensic-Performance-Audit.md#14-future-capability--daily-picks-yesterday-comparison) and [Portfolio's Historical Timeline](Portfolio-Page-Forensic-Performance-Audit.md#106-historical-timeline) — a future implementation should evaluate one shared snapshot/diff mechanism across all three features rather than three separate ones.
- **Historical qualification timeline.** A per-stock view of which screens it passed/failed over time — depends directly on the same persisted-snapshot prerequisite as screen entry/exit changes; not separately buildable first.
- **Industry ranking.** Rank a qualifying stock against same-industry peers on the scorecard's own metrics — reuses the existing Heatmap sector/industry grouping infrastructure (`backend/services/heatmap_service.py`) rather than a new taxonomy, consistent with every other feature in this codebase that needs sector/industry grouping.
- **Moat intelligence.** A qualitative competitive-advantage assessment — this is a genuinely new data/reasoning capability with no existing structured source in this codebase today (no moat-classification field exists anywhere in `stock_fundamentals_cache`); would need its own feasibility study before being scoped, mirroring this codebase's established practice of a feasibility study before full-scope coding (e.g. Epic 003's India Feasibility Study precedent).
- **Management intelligence.** Governance/promoter-quality narrative beyond the existing pledge/holding-percentage checks — for IN, could extend the existing promoter-pledge/holding fields already in the scorecard; for US, would need a new data source (no equivalent management-quality field exists today).
- **Capital allocation score.** A dedicated score for capital-deployment quality (buybacks, dividend policy, reinvestment efficiency) — Growth Intelligence Engine already has a "Reinvestment Efficiency" category (confirmed in this codebase's Growth Intelligence sprint history) that should be evaluated for reuse before building a new capital-allocation metric from scratch.
- **Business quality timeline.** A multi-year view of the underlying Business Quality Engine score (where available) — directly depends on resolving Section 3's confirmed IN Business Quality integration gap first; not separately meaningful until that data flows into Multibagger at all.
- **AI research summary.** A narrative synthesis of all of the above for one stock — this is, concretely, a Daily-Picks/Multibagger-specific application of the future Research Analyst (Epic 008), not a separate summarization feature; must inherit EPIC-008's full evidence-grounding and non-advisory rules.
- **Portfolio integration.** Cross-reference against a user's actual holdings (Section 4/9) — depends on no new Multibagger-side work, only a join against the existing `portfolio_holdings` table by symbol/market.
- **Daily change intelligence.** "Why did this stock's scorecard score change since the last refresh" — depends on the same persisted-snapshot prerequisite as screen entry/exit changes, plus a diff of the `checks[]` array specifically (which individual checks flipped, not just the aggregate score).
- **Research citations.** Attribute each qualitative/quantitative claim to its underlying data source and as-of date — directly reuses the evidence-disclosure discipline already specified for the Research Analyst ([EPIC-008 §6](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#6-evidence-and-grounding-model): source category, as-of timestamp, direct-evidence-vs-inference distinction) rather than inventing a separate citation format for Multibagger specifically.

**Sequencing note.** Several items above (screen entry/exit, historical timeline, daily change intelligence) share one prerequisite — a persisted daily snapshot — and several others (moat intelligence, management intelligence, AI research summary) depend on Epic 008 reaching a usable phase. "Why this qualifies" and "Portfolio integration" are the two lowest-risk, no-new-data-required items and are recommended as the first scheduled work if this capability is pursued.
