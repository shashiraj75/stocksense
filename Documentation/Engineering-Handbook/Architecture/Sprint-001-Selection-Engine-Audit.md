# Sprint #001 — Selection Engine Engineering Audit

**Auditor role:** Chief Quantitative Software Auditor
**Scope:** The Selection Engine — `backend/services/prediction_engine.py`, `daily_picks.py`, `quality_factors.py`, `multibagger_scorecard.py`, `screener_data.py`, `us_fundamentals.py`, `market_data.py`, `global_context.py`, `technical_indicators.py`, `news_sentiment.py`, and the `alpha_engine/` subpackage (`ic_engine.py`, `meta_model.py`, `optimizer.py`, `regime_cluster.py`, `outcome_logger.py`, `weight_adapter.py`, `store.py`).
**Method:** Static code review + direct evidence extraction (line counts, grep-verified thresholds, confirmed absence of test infrastructure). No code was modified, refactored, or optimized in the production of this audit.

---

## Executive Summary

| Dimension | Grade (/10) | One-line verdict |
|---|---|---|
| **Overall** | **5.0** | Sophisticated, well-reasoned investment logic let down by software-engineering discipline — no tests, no typed data contracts, no central threshold registry. |
| Architecture | 5 | Good subpackage design in `alpha_engine/`; two 1,800+ line god-files (`prediction_engine.py`, `quality_factors.py`) carry most of the system's risk. |
| Investment Logic | 6 | Individually well-justified factors; undermined by uncoordinated threshold proliferation and one *proven* over-tight gate. |
| Engineering | 4 | Zero portfolio awareness, zero post-publication monitoring, and a confirmed live production bug (prepared-statement collisions) that silently degraded the learning pipeline until caught by chance. |
| Code Quality | 4 | Strong naming/comments/external docs; undone by zero automated tests and inconsistent `print()`-based logging. |
| Explainability | 6 | Genuinely improved this session (reasoning, bull/bear case, demotion messages); still lacks explicit invalidation criteria. |
| Testing | **1** | No `test_*.py`, no `conftest.py`, no pytest config anywhere in the repository. This is the single most severe finding in the audit. |

**Read this first:** the Selection Engine's *ideas* (factor-based scoring, IC-weighted horizons, hard quality gates, regime-aware risk weighting) are institutionally credible and, in several places, genuinely well executed. The risk in this system is almost entirely in *engineering discipline*, not investment philosophy: nothing verifies that a future change won't silently break what's here today, because nothing automated checks it.

---

## 1. Architecture

| Sub-area | Grade | Evidence |
|---|---|---|
| Module structure | 5 | `backend/services/` is a flat directory of 30+ files with no subpackage boundaries except `alpha_engine/`. `prediction_engine.py` is **1,886 lines / 26 methods**; `quality_factors.py` is **1,831 lines / 23 functions**. Together these two files are ~44% of the Selection Engine's total line count (8,441 lines across the files audited). |
| Separation of concerns | 4 | Weak at file level — `PredictionEngine` (one class) owns technical scoring, fundamental scoring, sentiment aggregation, the quality gate, risk penalty, the confidence engine, trade-level math, deep-fundamentals blending, *and* (added this session) two confidence-adjustment functions. Reasonable at function level — each sub-score is its own method, in principle independently testable (nothing currently tests any of them). |
| Cohesion | Mixed | `alpha_engine/` subpackage: **strong** — `ic_engine.py` does only IC math, `optimizer.py` only portfolio math, `regime_cluster.py` only regime detection, `meta_model.py` only the ML model. `prediction_engine.py`/`quality_factors.py`: **weak** — many unrelated financial concepts bundled per file. |
| Coupling | 4 | High fan-in/fan-out through `prediction_engine.py` (imports `quality_factors`, `screener_data`, `global_context`, `news_sentiment`, `alpha_engine.ic_engine`, `fundamentals_cache`). The deeper risk: the shared `info` dict threaded through almost everything is an **untyped, implicitly-shaped dictionary** mutated in place by multiple functions (`augment_info_with_screener` bolts a nested `_screener_data` sub-dict onto yfinance's raw `.info` for IN stocks) — there is no schema, dataclass, or Pydantic model defining what keys exist when. Anyone touching this dict needs full tribal knowledge. |
| Scalability | 5 | Several real production bottlenecks were found and fixed *this session* (event-loop-blocking calls in 4 functions, a missing `prepare_threshold=None` causing prepared-statement collisions, a Postgres pool capped at `max_size=5`). Horizontal scaling is unaddressed: trained `meta_model` `.pkl` files are saved to **local disk**, not Postgres/object storage — they would not be shared across multiple backend replicas and are wiped on every redeploy (a known, documented, unfixed gap). |
| Maintainability | 4 | Hurt by: (a) zero tests, (b) untyped dict-based data flow, (c) duplicated/inconsistent thresholds (Section 3), (d) `print()`-based logging instead of structured logging, (e) two monolithic files. |

---

## 2. Data Flow

- **Sources:** yfinance (IN regime/fallback quotes + *all* US fundamentals/technicals), screener.in (IN fundamentals, authenticated HTML scrape), NSE official API (IN live quotes, FII/DII), Finnhub (quote fallback, company profile), Yahoo/Google News RSS (sentiment), plus global macro pulled via yfinance again. **No unified data-source abstraction exists** — each source has its own bespoke client (`nse_client.py`, `finnhub_client.py`, `screener_data.py`) with no shared interface/contract.
- **Validation:** Inconsistent and ad hoc. No schema validation anywhere; a malformed yfinance response shape propagates silently as missing data rather than failing loudly.
- **Missing-data handling:** Good *intent* (extensive `is not None` guards, NSE→Finnhub→yfinance fallback chains for quotes), poor *consistency* — each function invents its own convention for "I don't know" (`None` vs `0` vs `50` neutral-default vs, until fixed this session, a literal string `"Unknown"`). The exact bug class this produces (`sector_strength_score` returning the literal string `"Unknown"` instead of `None`) was found and fixed in this same engagement — direct proof the pattern is fragile.
- **Stale-data handling:** Mixed by design. Quote cache TTL 60s, prediction cache TTL 15min, fundamentals cache TTL 12h, Multibagger table refreshed nightly, Daily Picks frozen until next generation run (by design, not a bug — confirmed and disclosed to the user this session via a frontend staleness warning, not a backend fix).
- **Duplicate calculations — confirmed, with exact evidence:** ROE/ROCE/Debt-to-Equity are independently fetched or re-derived in at least four places:
  - `screener_data.py` — scraped directly from screener.in (IN), percentage-scaled (`roe_pct`, `roce_pct`).
  - `us_fundamentals.py` — ROCE *derived* from `EBIT / (Total Assets − Current Liabilities)` for US (yfinance has no native ROCE field).
  - `quality_factors.py` and `prediction_engine.py` — read raw yfinance fractions directly off `info` (`info.get("returnOnEquity")`, `info.get("returnOnCapitalEmployed")`), requiring manual `* 100` conversions scattered inline (e.g. `quality_factors.py:1152`).
  - `multibagger_scorecard.py` — reads pre-scaled percentage fields from the nightly-refreshed `stock_fundamentals_cache` table (`stock.get("roe_pct")`, `stock.get("roce_pct")`).

  **Five independently-chosen debt-to-equity thresholds exist across just two files** (verified by direct grep): `50%`, `150%` (×2), `200%`, `300%` (×2), `500%` — no shared constant, no single source of truth for "what counts as high leverage." This exact class of bug (a metric compared against an inconsistent scale/threshold in two places) is what caused the `min_roe` comparison bug found and fixed earlier in this engagement.
- **Caching:** At least six independent reimplementations of "in-memory dict cache with a manual TTL check" exist across `market_data.py`, `screener_data.py`, `news_sentiment.py`, `ic_engine.py`, and others — no shared caching utility or decorator. The exact failure mode found and fixed this session (a cache that stores *failures* for the full TTL, "poisoning" a symbol for hours after one rate-limited request) could plausibly exist, unfixed, in any of the other five ad hoc caches, since each was built independently.
- **Error handling:** Defensive but conflating. Heavy, repeated use of bare `except Exception: pass` / `except Exception as e: print(...)`. This is a reasonable resilience instinct for a best-effort scoring pipeline, but it makes no distinction between "this external data source is temporarily down" and "this code has a bug" — both disappear into the same catch-all, with no alerting layer surfacing either.

---

## 3. Investment Logic — Factor-by-Factor Review

For each factor: *why, weighting, duplication, decision-quality impact, horizon-specificity.*

### ROE
- **Why:** profitability/capital-efficiency — a legitimate, well-established factor.
- **Weighted via four independent mechanisms**, each with its own bar: hard-reject gate (`< -10%` severely negative), risk penalty (`< -5%`), quality-factor scoring (`> 18%` "good," with a 5-year-average comparison), Multibagger checklist (`> 18%`, same number, different code path). Not double-*added* to one sum, but independently *re-judged* four times with no shared definition.
- **Horizon-specific:** No — the same ROE thresholds apply at all three horizons; only the surrounding "quality" bucket's overall *weight* shifts by horizon via the IC engine.

### ROCE
- **Why:** capital efficiency — central to the Multibagger "quality compounder" thesis.
- **Three different thresholds for the same metric:** `> 8%` (today's order-book exception), `> 15%` (Multibagger checklist), `> 15%` (Elite Strong Buy tier, independently re-stated).
- **Duplicated source-of-truth:** screener.in scrape (IN) vs. derived `EBIT/(Assets−CurrentLiabilities)` (US) vs. re-read from the nightly cache table (Multibagger) — three computation paths for one concept.

### Debt / Leverage
- **The clearest, most quantifiable finding in this audit.** Seven hardcoded D/E comparisons found across two files (`prediction_engine.py`, `multibagger_scorecard.py`): `50%`, `150%`, `150%`, `200%`, `300%`, `300%`, `500%`. Five separate judging mechanisms (hard-reject, risk-penalty tier 1, risk-penalty tier 2, Multibagger checklist, Elite tier, today's order-book exception) each independently decided what "too much debt" means, with no shared constant and no documented rationale for why the cutoffs differ. **The system cannot currently answer, in one sentence, "is 160% D/E acceptable?"** — the answer depends entirely on which of the five mechanisms is asked.

### Cash Flow (OCF / FCF)
- **Why:** earnings-quality check — distinguishes cash-generative businesses from accounting-profit-only ones. Central to the single largest investment-logic change made this session.
- **Genuinely horizon-specific** (one of the few true logic differences in the system, not just a weight): the hard-reject OCF check is explicitly skipped for `horizon == "short"` (`prediction_engine.py:1360`) — "growth stocks may have negative OCF short-term."
- **Proven both valuable and flawed in the same engagement:** the original all-or-nothing rejection correctly excluded cash-burning businesses, but also wrongly excluded three real, fast-growing order-book-driven companies (HFCL, Apollo Micro Systems, ideaForge) until refined this session with a multi-condition exception (revenue growth, leverage, ROCE/earnings-trend).
- **Unverified risk, not a confirmed bug:** OCF/FCF figures are sourced from yfinance fields (`operatingCashflow`/`operatingCashflows`) in the gate, vs. a separately-cached `operating_cf_latest_cr` figure in Multibagger — different units/sources for nominally the same number, never directly cross-checked for agreement.

### Growth (Sales / Profit)
- **Why:** forward-looking factor; doubles as the "order-book execution" proxy introduced this session.
- **At least four different growth thresholds in active use:** `>10%` (order-book exception, and again independently in the Elite tier), `>12%` (Multibagger checklist 3Y), `>15%` (Multibagger Discovery SQL screen, and the order-book exception's *own* revenue-growth bar — worth flagging: the order-book exception was implemented with a `>15%` revenue-growth condition but is informally described in places as a "10%" threshold; this is a real precision gap between code and description, not just a design choice).
- **US asymmetry, honestly documented:** US growth figures are 3-year-only (`sales_growth_5y_pct` is always `None` for US, per code comment) because yfinance's free tier caps annual financials at 4 years — correctly disclosed in-code rather than silently producing a wrong number. This is a good practice example in an otherwise inconsistent area.

### Valuation (P/E, EV/EBITDA, Price/Sales)
- **Proven, not theorized, over-tightness.** Live data pulled this session showed Pidilite (P/E 65.2), Asian Paints (57.7), Havells (43.9), and Nestlé India (78.1) — all passing every other Quality Compounder check cleanly — failing **only** on `P/E < 35`. This single hardcoded cutoff was, on the day tested, the entire reason the Quality Compounder screen returned zero results, despite several genuinely excellent businesses being available.
- **Confirmed structural redundancy:** `P/E < 35` is enforced *both* as the screen's SQL `WHERE` clause *and* as a separate scorecard `_check()` entry. Because the SQL filter already excludes anyone who'd fail it, the checklist item can **never actually fail** for any stock that reaches scoring — it silently and permanently awards a point that was never genuinely at risk, inflating every survivor's score by one check it can't lose.

### RSI / MACD / EMA (Technical)
- **The cleanest factor in the system from a software-engineering standpoint.** Computed exactly once (`compute_indicators(df)` in `technical_indicators.py`) and consumed everywhere downstream — no duplication found.
- **Genuinely well-designed horizon weighting:** confirmed IC values are `0.055` (short) → `0.038` (medium) → `0.018` (long) for IN — momentum's forecasting power is correctly modeled as decaying over longer windows, a textbook-correct quant judgment, expressed cleanly through the weighting mechanism.

### News / Sentiment
- Horizon-weighted the same well-reasoned way as technical (highest short, lowest long).
- **Two independent RSS-fetch implementations** exist (`_fetch_rss` for per-stock news, `_fetch_macro_rss` for macro news) rather than one parameterized function — confirmed this session when only the macro path turned out to have an event-loop-blocking bug the per-stock path had already avoided. Parallel, divergent implementations of the same underlying task.
- **Methodologically the most primitive component relative to the rest of the system** — VADER sentiment + bullish/bearish keyword counting, no contextual NLP, no source-credibility weighting, no explicit recency decay. Reasonable for a zero-subscription-cost tool; the weakest link if compared to the sophistication of the IC-engine/regime/quality machinery surrounding it.

### Macro (Global Context)
- Contributes as a *direct* `global_macro` factor **and again indirectly** by driving the 4-state regime classification (`BULL_CALM`/`BULL_VOLATILE`/`BEAR_CALM`/`BEAR_PANIC`), which then re-weights every *other* factor via `REGIME_WEIGHT_MULTIPLIERS`. This gives macro a structurally amplified, double-counted-in-effect influence on the final score. This may well be an intentional risk-management design choice (macro *should* dominate during a real regime shift) — but it is not documented anywhere as a deliberate choice, and its magnitude has not been measured.
- **Confirmed, genuine gap:** `REGIME_WEIGHT_MULTIPLIERS` (`regime_cluster.py:42-47`) is keyed only by regime label — there is no horizon dimension. A `BEAR_PANIC` regime applies the *identical* multiplier table to a 5-day short-term call and a 3-year compounder thesis, despite the two having obviously different sensitivities to a short-lived panic.

### Portfolio (Optimizer)
- `optimizer.py` (121 lines) is a clean, well-isolated mean-variance optimizer (Ledoit-Wolf shrinkage covariance, 40%-per-position cap, regime-modulated risk aversion) — no duplication, good cohesion, legitimate Markowitz-style approach to the question it answers.
- **The question it answers is narrow:** it diversifies the day's 6 new picks *against each other only*. See Section 8 — it has zero visibility into anything a user already owns.

---

## 4. Quality Gates

- **Hard Reject** (`_quality_gate`, `prediction_engine.py:1340`): deliberately minimal, 4 checks — severely negative ROE/margin, non-positive OCF (medium/long only, with this session's order-book exception), extreme leverage (D/E > 500%). Financial-sector stocks are correctly exempted from the OCF/leverage checks with a documented Ind-AS accounting rationale.
- **Soft Reject:** not a named, unified concept. What *functions* as soft rejection today is an emergent combination of three independently-built mechanisms living in three different places: the risk-penalty subtraction (existing), and two confidence-demotion functions added *this session* (risk/reward, promoter pledge). They overlap conceptually but were not designed as one system.
- **Missing checks, confirmed by absence:** no auditor-change/related-party-transaction checks; no promoter-pledge *acceleration* check (only the latest snapshot — explicitly disclosed as a known limitation in `multibagger_scorecard.py`'s own docstring); no forensic fraud-detection heuristic (e.g. Beneish M-Score) anywhere, despite both Altman Z-Score (distress) and Piotroski F-Score (quality) being implemented. **Distress and quality scoring exist; fraud-risk scoring specifically does not** — a depth inconsistency worth naming explicitly, since the presence of Altman/Piotroski could be mistaken for fraud coverage.
- **Governance:** reasonably covered for IN (promoter pledge, promoter holding trend, FII/DII flow); thinner for US (no pledge-equivalent concept exists for US filings, by necessity; the closest "integrity" proxy is low short-interest, a weak signal for management integrity specifically).
- **Accounting quality:** an accruals-ratio dimension is computed and folded into the broader quality score — a legitimate, if basic, earnings-manipulation-risk proxy — but it is not surfaced prominently and does not itself gate anything.
- **Financial distress:** Altman Z-Score is computed and scored, but **not wired into the hard gate** — a stock in the Altman distress zone loses points but is not automatically excluded the way negative-OCF or extreme-leverage stocks are, despite Altman Z being a more validated distress predictor than some things that *do* trigger hard rejection.

---

## 5. Horizon Logic

**Direct finding: the system is overwhelmingly weight-differentiated, with a small number of genuine logic differences.**

Confirmed genuine *logic* (not weight) differences:
1. OCF hard-reject is skipped entirely for `short` (`prediction_engine.py:1360`).
2. Deep-fundamentals analysis is skipped entirely for `short`, and blended at a different ratio for medium (0.3) vs. long (0.6) — a structural branch, not a scalar.
3. Trade-level stop-loss formulas (`_trade_levels`) use different ATR multiples and floor logic per horizon bucket.
4. Outcome-resolution windows differ structurally (1D/5D short, 5D/20D medium, 60D-only long) — what "success" even means is horizon-specific.

Everything else — the majority of the system — is the *same* algorithm fed *different numbers*: IC factor weights, `_dynamic_weights` volatility/regime modulation, and quality-factor sub-dimension weighting all use one formula across all three horizons, varying only the input weight table.

**Terminology mismatch worth flagging directly:** the audit brief's example horizons (Short, Swing, Strategic, Compounder) do not map onto the codebase's actual three horizons (short/medium/long). There is no "Swing" tier distinct from "Short," and no horizon literally named "Compounder" — Multibagger is the closest conceptual analog, but it is **architecturally a separate system**, entirely outside the horizon-based prediction engine, with no horizon dimension of its own. If a 4-tier horizon model is the intended product direction, it does not exist today.

---

## 6. Explainability

- **Why:** strong — the `reasoning` array (indicator/signal/reason triples) gives a genuinely human-readable account of every contributing factor.
- **Why now:** partial — some macro/regime lines provide loose temporal context ("Market in uptrend — 3.8% gain over 3 months"), but there is no explicit "what changed since last time" diff surfaced anywhere; the same stock's reasoning list looks nearly identical day to day unless an underlying number moves enough to flip a bullet.
- **Risks:** `bear_case` exists and was meaningfully extended this session (explicit risk/reward and governance-risk messages added). Still not comprehensive — no explicit liquidity-risk or currency/FX-risk callouts, despite a liquidity quality dimension already being computed elsewhere.
- **Invalidation:** **not a first-class concept anywhere.** No prediction states "this thesis is wrong if X happens." The stop-loss level is the closest functional analog, but it is framed purely as trade management, never labeled or reasoned about as a thesis-invalidation trigger — a real gap relative to standard institutional research-note conventions.
- **Monitoring:** see Section 9 — confirmed absent. The Portfolio "Signal" tooltip and Paper Trade "Entry Signal" relabeling (both added this session) are *disclosures of this gap to the user*, not closures of it.

Explainability is the area that received the most direct, concrete improvement during this engagement (five separate additions: risk/reward demotion message, pledge-risk message, REJECTED-reason surfacing, "Entry Signal" relabeling, "Allocation" tooltip) — a genuinely positive trajectory, still short of the two structurally larger pieces (invalidation criteria, continuous monitoring).

---

## 7. Confidence

- **How it is calculated today:** `confidence` is a single scalar **linearly derived from `composite_r`'s distance from the BUY/SELL threshold** (`(composite_r − 60) / 40 × 100` for BUY) — it is a re-expression of the same composite score on a different scale, not an independent measurement of certainty. Two functions added this session (`_apply_risk_reward_adjustment`, `_apply_pledge_adjustment`) can cap it downward to 30; nothing moves it up independently, and nothing distinguishes *why* a given value is low.
- **A second, genuinely independent confidence concept already exists, underused:** `_confidence_engine` produces `confidence_score`/`confidence_band` from five distinct components — `data_completeness`, `factor_agreement`, `earnings_stability`, `regime_certainty`, `historical_factor_reliability`. This is materially closer to the multi-dimensional framework the audit brief asks about than anything new would need to be.
- **Direct evidence this is already under-distinguished even by the people building it:** earlier in this same engagement, a UI label collision between "Confidence" and "Conviction" (both referring to the *first*, simpler scalar) had to be found and fixed — strongly suggesting the *second*, richer confidence concept is not well understood or consistently surfaced, even in active development.
- **Should multiple dimensions exist:** functionally, fragments of exactly this (Evidence/Data/Decision Confidence) already exist, unintegrated:
  - `confidence_breakdown.data_completeness` ≈ **Data Confidence**
  - `confidence_breakdown.factor_agreement` ≈ a form of **Evidence Confidence**
  - the top-line `confidence` ≈ **Decision Confidence**

  This is a **consolidate-and-name** opportunity, not a build-from-scratch one — the computation already exists; it has never been organized around an explicit three-dimension model or surfaced that way to users.

---

## 8. Portfolio Awareness

**Confirmed by direct search: zero.** `optimizer.py`, `daily_picks.py`, and every file in `alpha_engine/` contain no reference to `portfolio_holdings` anywhere. The optimizer's covariance matrix is built only across that day's 6 new candidates, against each other — never against anything a user already owns.

Concretely missing:
1. **No concentration-risk check** — a user already 40% allocated to one sector receives the identical generic picks as anyone else.
2. **No correlation check against existing holdings** — only candidate-vs-candidate correlation is considered.
3. **No position-sizing awareness of account size/existing exposure** when suggesting Paper Trade quantities.
4. **Multibagger and Daily Picks never cross-reference Watchlist or Portfolio** — every user sees byte-identical results regardless of their own holdings, despite the underlying holdings/watchlist data already living in the same Postgres database.

This is one of the most underdeveloped areas relative to how little new infrastructure would be required to close it — the data already exists; it is simply never joined against the recommendation engine.

---

## 9. Monitoring

**Confirmed: no active post-publication monitoring of live predictions exists.**

- `outcome_logger.py` resolves predictions for **statistical/training purposes** (feeding the IC engine and meta-model) — it does not re-notify a user that a specific call has weakened. This is a different purpose entirely from monitoring, and should not be conflated with it.
- Paper Trade has live monitoring (`trade_notifier.py`) — but only for **price proximity to a static target/stop** set at trade-open time. It never re-evaluates whether the underlying AI *signal* has changed. A trade can be flagged "near target" while the model's live opinion has flipped to SELL, with the user never told about the signal change specifically.
- Daily Picks and Multibagger have no equivalent at all — once generated, a pick is a static artifact until the next full regeneration. Nothing ties a specific historical recommendation to a later re-evaluation or notifies on change.

Missing capabilities, concretely: (a) signal-change notifications beyond price-target proximity; (b) a "thesis health" indicator that updates between official regeneration cycles as new data arrives; (c) any user-facing changelog of "this call flipped from BUY to HOLD on [date], here's why."

---

## 10. Code Quality

- **Complexity:** Two files (`prediction_engine.py`, `quality_factors.py`) carry the overwhelming majority of business logic. `PredictionEngine` is effectively a god-class spanning unrelated concerns. No complexity-measurement tooling (`radon`, `flake8`, or equivalent) is configured anywhere in the repository.
- **Naming:** A genuine strength. Function names are consistently verb-first and intent-revealing (`_quality_gate`, `_compute_risk_penalty`, `_apply_risk_reward_adjustment`). Comment quality is noticeably above average for a codebase this size, particularly comments explaining *why* a decision was made, not just what the code does — and this improved further with several rounds of fixes during this engagement that added detailed rationale at each change site.
- **Documentation:** Excellent at the function/comment level; a comprehensive, actively-maintained external `STOCKSENSE_DOCUMENTATION.md` (135K+ characters) exists — unusual and commendable for a project this size. No architecture-decision-record system exists prior to this audit (the Engineering Handbook this audit lives in was created as an empty skeleton in this same session, immediately before this report).
- **Testing — the single most severe finding in this audit.** Direct filesystem search confirms: **zero** `test_*.py` files, **zero** `conftest.py`, **zero** pytest configuration anywhere in the repository. This must not be confused with the Validation engine (`validation_engine.py`) — that is a walk-forward *model-backtesting* tool answering "were the model's historical predictions accurate," a quant-research concept. It answers nothing about whether the *code* behaves correctly. Every verification performed during this entire multi-session engagement was manual, live, production-curl-based testing — effective for catching the specific bugs found, but leaving **no regression safety net**: any future change could silently break any fix made here, with no automated signal to catch it.
- **Performance:** Several confirmed, real performance defects were found and fixed *during this engagement alone* — four separate event-loop-blocking functions, a missing `prepare_threshold=None` causing silent prepared-statement collisions in production, and under-batched concurrent request handling. The fact that these existed, unnoticed, prior to this session indicates a systemic gap in performance-testing/observability practice, not merely isolated incidents.
- **Error handling:** Defensive-by-default, which prevents hard crashes but renders most failures silent. The prepared-statement production bug was caught only because a log stream happened to be read live during this engagement — not because any monitoring or alerting layer surfaced it.
- **Logging:** Confirmed inconsistent by direct count. `daily_picks.py` has 31 `print()` calls and zero use of the `logging` module; `weight_adapter.py` has 11 and zero; `meta_model.py` has 7 and zero. Only `prediction_engine.py` (8 prints, 3 logging calls) and `market_data.py` (0 prints, 5 logging calls) use `logging` at all, and even then inconsistently. No log levels, no structured/JSON logging, no correlation IDs exist anywhere — production debugging depends entirely on manually reading Railway's raw log stream, exactly as was required multiple times this session.
- **Extensibility:** Genuinely good in `alpha_engine/` — the IC-engine/factor-weight design proved tractable to extend, evidenced by the successful addition of full US-market support and IN/US learning-engine separation within this same engagement. Conversely, the threshold-proliferation problem (Section 3) makes the quality-gate/risk-penalty/checklist area progressively *riskier* to extend — each new threshold added (several were, this session) increases the surface area of an already-uncoordinated set of cutoffs with no central registry to reconcile them against.

---

## Strengths

- Factor-based, IC-weighted scoring design is methodologically credible and, for technical/sentiment factors specifically, genuinely well horizon-tuned.
- `alpha_engine/` subpackage demonstrates the team can build cohesive, single-responsibility modules when given the chance — it is the architectural high-water mark of the codebase.
- Hard quality gate is deliberately minimal and scoped (4 checks), with a documented financial-sector exemption rationale — a sign of restraint rather than gate-sprawl, at least at that one layer.
- Comment and external-documentation quality are well above average for a project this size, and demonstrably improving in real time.
- Demonstrated, in-session capacity to find and fix real production defects quickly once identified (event-loop blocking, prepared statements, the order-book quality-gate exception) — the team's debugging and iteration speed is a genuine asset.
- Honest, in-code disclosure of known limitations (US 5-year-growth cap, single-snapshot pledge-trend checks) rather than silently faking unavailable data — a good practice pattern worth preserving and extending.

## Weaknesses

- No automated testing of any kind — the single largest risk in the entire system.
- Two monolithic files (`prediction_engine.py`, `quality_factors.py`) concentrate most business logic and most risk.
- Untyped `info` dict as the primary data-flow vehicle, with no schema/contract.
- At least five independently-chosen debt-to-equity thresholds, three-plus independently-chosen ROCE/growth thresholds, with no central registry.
- Inconsistent, mostly unstructured logging (`print()` over `logging` in the majority of files).
- Zero portfolio awareness in the recommendation engine.
- Zero post-publication signal-change monitoring.

## Critical Issues

1. **No automated tests anywhere.** Every fix made in this engagement (and presumably every prior one) carries no regression protection.
2. **Confirmed, proven over-tight valuation gate.** `P/E < 35` was, on tested live data, single-handedly excluding multiple genuinely excellent Indian compounders from the Quality Compounder screen, and is structurally redundant (enforced identically in both the SQL filter and the scorecard checklist).
3. **Untyped, implicitly-shaped shared data structure (`info` dict)** as the backbone of nearly the entire Selection Engine, with no schema validation.
4. **Zero portfolio-context awareness** in any recommendation surface (Daily Picks, Multibagger, the optimizer) despite the required data already existing in the same database.

## High Priority Improvements

- Introduce a baseline automated test suite for the Selection Engine's pure-logic functions (`_quality_gate`, `_compute_risk_penalty`, the IC engine's weight derivation, the optimizer) — these are largely deterministic given fixed inputs and are tractable to unit-test without needing live data.
- Establish a single, centralized threshold registry (even a plain constants module) for ROE/ROCE/D-E/growth/valuation cutoffs, replacing the current pattern of independently hardcoded numbers scattered across five-plus files.
- Resolve the `P/E < 35` redundancy and re-examine its calibration against the proven evidence that it currently excludes high-quality, high-multiple compounders.
- Define a typed contract (dataclass or Pydantic model) for the `info` dict's expected shape, at minimum documenting which keys are guaranteed present after augmentation.

## Medium Priority Improvements

- Consolidate the three confidence-adjacent computations (`confidence`, `confidence_score`/`confidence_band`, the two new adjustment functions) into one explicitly-named, three-dimension framework (Evidence / Data / Decision Confidence), surfaced consistently in the UI.
- Add explicit invalidation criteria to each recommendation, distinct from the stop-loss level.
- Make `REGIME_WEIGHT_MULTIPLIERS` horizon-aware rather than applying one multiplier table uniformly across short/medium/long.
- Replace `print()`-based output with structured logging (levels, at minimum) across `daily_picks.py`, `weight_adapter.py`, `meta_model.py`, and the other `alpha_engine/` modules.
- Unify the two parallel RSS-fetch implementations in `news_sentiment.py` into one parameterized function.

## Nice-to-Have Improvements

- Wire Altman Z-Score distress-zone results into a soft- or hard-reject signal, rather than leaving it purely informational.
- Add a basic forensic/fraud-risk heuristic (e.g. an accruals-anomaly or Beneish-style check) to close the gap between "we score quality and distress" and "we screen for fraud," which are not currently the same thing despite surface appearances.
- Consider persisting trained `meta_model` artifacts to Postgres/object storage instead of local disk, so they survive redeploys and could eventually be shared across horizontally-scaled replicas.

## Estimated Engineering Effort

| Workstream | Rough size | Notes |
|---|---|---|
| Baseline test suite for pure-logic functions | L | No existing test infra to build on; needs fixtures for `info`-shaped test data. |
| Centralized threshold registry | M | Mechanical but touches 5+ files; needs care not to change live behavior while consolidating. |
| `P/E < 35` redundancy + recalibration | S–M | The redundancy fix is small; the recalibration question is a judgment call requiring stakeholder input, not just engineering. |
| Typed `info` contract | M–L | Cuts across nearly every Selection Engine file; best done incrementally. |
| Confidence framework consolidation | M | Mostly a renaming/surfacing exercise on top of existing computation. |
| Portfolio-aware recommendations | L–XL | Requires real product decisions (how should existing holdings change a recommendation?) before engineering can proceed. |
| Post-publication monitoring | L–XL | New, ongoing background-job surface area; needs its own notification-fatigue design. |
| Structured logging rollout | M | Mechanical, but touches most files; best paired with a logging-conventions decision first. |

## Recommended Sprint Roadmap

- **Sprint 2 — Safety net first.** Stand up the test suite for pure-logic functions (gate, penalty, IC weights, optimizer) before touching any of the threshold/logic issues below — every subsequent change becomes safer once this exists.
- **Sprint 3 — Threshold consolidation.** Centralize ROE/ROCE/D-E/growth/valuation thresholds; fix the `P/E < 35` SQL/checklist redundancy; revisit its calibration with the live evidence already gathered.
- **Sprint 4 — Data contract.** Introduce a typed shape for the `info` dict, starting with the fields the quality gate and risk penalty depend on most heavily.
- **Sprint 5 — Confidence & explainability.** Consolidate the confidence framework; add explicit invalidation criteria; make regime multipliers horizon-aware.
- **Sprint 6 — Portfolio awareness (product-led).** Scope what "portfolio-aware recommendation" should mean with product/founder input before engineering the optimizer/Daily Picks integration.
- **Sprint 7 — Monitoring.** Design and build post-publication signal-change tracking, paired with a notification-fatigue strategy so it doesn't degrade into noise.
- **Ongoing, parallel:** structured-logging rollout and accruals/fraud-risk heuristics can be picked up opportunistically alongside any of the above, since neither blocks nor is blocked by the others.

---

*This audit reviewed code only; no production behavior, data, or user-facing surface was changed in its production. All thresholds, line counts, and file structures cited above were verified directly against the current state of the repository at the time of writing.*
