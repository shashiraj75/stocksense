# Recommendation Consolidation Intelligence — Operational Activation & Consumer Exposure Decision (Epic 005, Sprint #009)

**Status:** Decision-only sprint. No production code, frontend code, API behavior, Railway configuration, or feature flag was modified. No prior history was rewritten.

## Evidence Checkpoint

Reviewed directly: EPIC-004 closure report, Sprint #001–#008 reports, SSDS-009, the Evidence Contract, Traceability/Versioning doc, Integration Path Decision, Live Stock Analysis Integration Readiness doc, Sprint #008's own report, `.env.example`, `services/recommendation_consolidation_api_composer.py`, `api/routers/predictions.py`, and the frontend's `frontend/src/utils/api.ts` / `Prediction` interface and `fetchPrediction`.

All non-negotiable facts re-confirmed directly from code, not assumed from memory:

| Fact | Status |
|---|---|
| Prediction Engine remains sole source of truth for signal/confidence/composite score/gates | **Confirmed** — `compose_prediction_response_with_rci` never touches these fields; verified by direct re-read |
| RCI is additive, read-only, deterministic, non-authoritative | **Confirmed** — composer only ever adds `recommendation_consolidation`; on any failure returns the original object reference unchanged |
| RCI creates no replacement signal/score/confidence/gate | **Confirmed** — Sprint #008's own `test_rci_payload_has_no_replacement_signal_or_confidence` plus direct field-list re-inspection (`api_composer.py:120-140`) |
| RCI deployed but disabled by default | **Confirmed** — `rci_live_stock_analysis_enabled()` returns `False` for unset/malformed env values (`recommendation_consolidation_api_composer.py:39-52`); `.env.example` lists it commented out |
| RCI does not affect Daily Picks/Portfolio/Watchlists/Alerts/Paper Trading/persistence | **Confirmed** — composer is invoked only inside `api/routers/predictions.py`'s cache-hit branch (`predictions.py:131-132`); 3 static-import tests confirm `daily_picks.py` never imports it |
| RCI does not mutate shared prediction-cache objects | **Confirmed** — shallow top-level merge only, verified line-by-line against `recommendation_evidence_adapter.py`/`recommendation_consolidation_engine.py` per Sprint #008's own audit, re-confirmed unchanged this sprint (no commits since `dc4cb53` touch these files) |
| RCI does not use legacy `growth_score`/`valuation_score` | **Confirmed** — composer reads only `business_quality`, `financial_strength`, `growth_intelligence`, `valuation_intelligence` keys |
| Business Quality fraud risk is an unresolved flag, not an enforced veto | **Confirmed** — Sprint #008's live `RELINFRA` spot-check: `unresolved_risk_flags` populated, `active_gates` empty |
| Financial Strength liquidity distress is an enforced gate where production already enforces it | **Confirmed** — Sprint #008's live `AAL` spot-check: `active_gates: ["Financial Strength: true_veto (enforced)"]` |
| Structural market coverage gaps are coverage notices, not company-specific conflicts | **Confirmed** — unchanged behavior since Sprint #003, re-validated live in Sprint #008 |
| Valuation Intelligence's kill-switch state is unchanged | **Confirmed** — both `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US` remain absent/commented in `.env.example`; no commit since Epic 004's closure touches `prediction_engine.py`'s switch logic |

**No contradiction found between this sprint's direct code inspection and any prior sprint's documented conclusion.** Sprint #008's own already-disclosed finding — that `CP-02`/`CP-03` are currently dormant in production because Valuation Intelligence's switches are off — is reconfirmed, not new, and is carried forward into §4 below rather than re-litigated.

### Frontend inspection result (new findings this sprint)

- `frontend/src/utils/api.ts`'s `Prediction` interface (`api.ts:54-68`) has **no `recommendation_consolidation` field**, and `fetchPrediction` (`api.ts:89-120`) returns `res.data as Prediction` — a compile-time type assertion, not a runtime parser or schema validator. Axios returns the raw JSON body unmodified; nothing strips, filters, or transforms unknown fields before they reach the caller.
- **Practical consequence**: if RCI were enabled today, the additive field would arrive over the wire intact, but would be **inaccessible to any existing TypeScript code** without first widening the `Prediction` interface. No existing rendering code reads it, so none would break — but no existing rendering code could safely consume it either, without that interface update.
- No caching, transformation, or response-filtering layer exists between `fetchPrediction` and its callers that would discard the field.
- **Conclusion: the current frontend safely ignores the additive field by omission, not by deliberate design** — it has never been told the field exists. This is a "harmless absence," not a "tested-safe presence."

## Current Deployed-State Inventory

| Item | State | Source |
|---|---|---|
| Latest commit on `origin/main` | `dc4cb53` | Confirmed in prior audit |
| Railway deployment | `dc4cb53` deployed, service online | User-confirmed visually (not independently verifiable from this repo) |
| `RCI_LIVE_STOCK_ANALYSIS_ENABLED` | Absent from Railway variables → disabled by default | User-confirmed visually |
| `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US` | Unchanged, disabled by default (Epic 004's own shipped state) | `.env.example`, no superseding commit |
| Frontend RCI UI | None exists | Confirmed by repo search — no component references `recommendation_consolidation` |
| Daily Picks | Unaffected | Static-import tests (Sprint #008), reconfirmed this sprint by absence of any new commit touching `daily_picks.py` |
| RCI persistence | None — computed per-request, never written to any database or cache beyond the existing prediction cache's pre-existing entry | `api_composer.py` performs no DB/file I/O |

## A. RCI API Activation

**Recommendation: remain disabled.** There is no operational reason to enable the flag in Railway today: nothing consumes the field (no frontend, no internal tooling), so enabling it would add the composer's measured ~0.1-0.4ms/call overhead and a new code path in the live response with zero present consumer — pure risk for zero realized value. Enabling it later for **controlled internal API testing only** (no frontend usage) is a reasonable, low-risk future step (see Path C), but is not justified as an immediate action absent a defined test plan with sign-off. It is not yet ready for user-facing rollout, since no UI exists to consume it responsibly.

## B. RCI User Exposure

Before RCI is shown to users on the live Stock Analysis page, the following must be completed:

1. Widen the frontend `Prediction` TypeScript interface to declare the optional `recommendation_consolidation` field with its full nested shape.
2. Design (not yet build) a dedicated UI section per §5 below, confirmed not to look like a second signal/recommendation.
3. Define explicit handling for the "evidence available but Valuation Intelligence disabled" state, since this is the live, current condition (per §4, State 1) — the UI must not imply complete coverage when one engine's confidence influence is feature-disabled.
4. Decide whether RCI is shown as "live" only, or also against stored Daily Picks snapshots — current architecture only supports the live path (RCI is not persisted), so any snapshot-comparison framing would require new work, not just UI.
5. Enable `RCI_LIVE_STOCK_ANALYSIS_ENABLED` in Railway only after the above are implemented and tested, not before.

## C. Valuation Intelligence Activation

**Should be considered independently, and before RCI user exposure, not alongside or after it.** Reasoning: RCI's own strongest, most evidence-validated patterns (`CP-02`/`CP-03`) are dormant while Valuation Intelligence is disabled (Sprint #008's own finding, reconfirmed above). Exposing RCI to users while withholding its most validated content means users see a feature that is real but unnecessarily impoverished. Valuation Intelligence's own activation question, however, has a different evidence shape and different risk (a live confidence-score influence, not an explanatory overlay) — it must be decided on its own merits (Epic 004's own outcome-validation evidence), not deferred to or merged with RCI's UI timeline. These are explicitly **not** the same decision and are evaluated separately in Path D.

## Evaluate Operational Paths

### Path A — Keep Both Disabled

Safest option, zero new risk, but defers all of RCI's explanatory user value and leaves Epic 004's already-completed validation work dormant indefinitely. UI design work *can* proceed without activation — none of the design work in §5 requires a live flag — so "keep disabled" does not block design progress. **Acceptable as a holding pattern, not a final answer**, since it makes no forward progress on either open decision.

### Path B — Keep Valuation Disabled; Prepare RCI UI First

High user-trust value if executed carefully (explains existing evidence, doesn't impersonate a second recommendation), moderate frontend effort (new section + interface widening + careful copy review), low operational risk (flag stays off in Railway throughout design). Must explicitly design for the "Valuation Intelligence feature-disabled" state from day one, not retrofit it later — per §4, State 1, this is the live condition the UI will actually face. Recommended UI section name: **"Evidence Summary"** (see §5) placed below the existing signal/confidence header, never inside it.

### Path C — Enable RCI API Only for Controlled Production Validation

Low risk if done correctly: the frontend currently ignores the field safely (per the Evidence Checkpoint finding above — by omission, not by tested design), payload-size impact is negligible (Sprint #008 measured sub-millisecond composition cost; the JSON payload itself adds a few KB at most), cache safety and error isolation are already proven (Sprint #008's regression suite), rollback is trivial (remove the Railway variable, redeploy). Logging exists (`log.warning` on failure) but no dedicated observability/metrics exist yet for RCI-specific success/failure rates — this would need to be added before this path is exercised, to make the validation meaningful rather than silent. **Not enabled this sprint**, per explicit instruction.

### Path D — Revisit Valuation Intelligence Activation First

Epic 004's own evidence (India ρ +0.272 n=81, US ρ +0.418 n=54, monotonic decile buckets, the AND cross-engine gate empirically re-confirmed live at Sprint #008) is sufficient to support an activation *decision*, not further validation — Epic 004's own closure recommendation already said as much. The asymmetric +2/-4 cap and AND-gate safeguard are both implemented and tested; no new engineering is required to activate. Open items before activation: gate-hit-rate telemetry (named in Epic 004's own Technical Debt Register as not yet wired into production monitoring) and an explicit choice of market-specific vs. simultaneous rollout. **This is the one concrete technical prerequisite this sprint identifies as still outstanding** — not a validation gap, an operational-readiness gap (monitoring).

### Path E — Another Evidence-Supported Path

No additional path is supported by direct evidence beyond A-D; none is proposed.

## State 1 — Valuation Intelligence Disabled (current, live condition)

- Meaningful patterns: Business Quality unresolved-flag narratives (`RELINFRA`-shaped), Financial Strength enforced-gate narratives (`AAL`-shaped), structural coverage notices for India Financial Strength gaps, broadly-aligned low-conflict narratives (`GOOGL`/`KO`/`NVDA`-shaped) — all confirmed live and meaningful without any valuation input.
- Dormant patterns: `CP-02`/`CP-03`, RCI's two most evidence-validated conflict types (cheap-valuation-vs-cautious-growth interactions) — confirmed dormant by direct kill-switch read, not inferred.
- Current narratives still provide real explanatory value for the four non-valuation engines; they do not become meaningless in Valuation Intelligence's absence.
- Coverage notices and unresolved flags are structured, named fields with deterministic text (per Sprint #003-#005's contract) — understandable to users only if the UI labels them clearly as "not currently enforced" vs. "currently blocking," a distinction the raw payload makes but a careless UI could blur.
- Risk of confusion without valuation evidence: **real but manageable** — the risk is a user assuming RCI's silence on valuation means "no concern," rather than "this evidence source is currently switched off." This is why an explicit "available evidence only" / feature-state disclosure is recommended, not optional, for any future UI build (§5).

## State 2 — Valuation Intelligence Enabled

- Additional available patterns: `CP-02`/`CP-03` become live, surfacing the cheap-valuation-plus-cautious-Growth interaction Sprint #005's cross-engine analysis specifically validated.
- The cheap-valuation-plus-Growth-avoid pattern is useful enough to justify activation **on its own evidentiary merits** (Epic 004's outcome-validation data), independent of whether RCI ever displays it — RCI's UI value is a secondary beneficiary of that decision, not its justification.
- Valuation's own safeguards (asymmetric cap, AND cross-engine gate) remain sufficient per Epic 004's closure rating ("Strong" architecture/reliability), with the one named caveat (`RELCAPITAL`-shaped "hold-only" gate gap) carried forward, unchanged, as accepted technical debt.
- RCI should explain a feature-disabled valuation state (State 1) differently from a genuine valuation disagreement (State 2, e.g. Valuation says cheap while Growth says avoid) — the current contract already distinguishes `feature_disabled` from `supported`/`mixed` statuses at the data layer (Sprint #003's own rule); a future UI must preserve, not collapse, that distinction in user-facing copy.

## Frontend / UX Readiness Review (design assessment only — no UI built)

| Candidate name | Clarity | Risk of being mistaken for a 2nd recommendation | Notes |
|---|---|---|---|
| "Investment Thesis" | Medium | **High** — "Thesis" implies a position/recommendation | Not recommended |
| "Evidence Summary" | High | Low — framed as supporting material, not a verdict | **Recommended** |
| "What Supports and Challenges This View" | High | Low-medium — clearly framed as for/against, but slightly verbose for a section title | Reasonable alternative |
| "Why This Signal?" | Medium | Medium — "signal" wording risks blending with the existing AI Signal label | Not recommended |

**Recommended placement**: below the existing AI Signal / Confidence / Fundamentals / Sentiment / RSI block, as a distinct, visually separated section — never interleaved with or directly under the confidence percentage, to avoid implying it modifies that number. On mobile, this section should collapse by default (accordion) given its informational density relative to the above-the-fold signal block.

**A future RCI UI must not display** (per explicit instruction, reconfirmed as a hard constraint by this sprint): a second BUY/SELL/HOLD label, a second confidence percentage, a master score, a hidden weighting system, or raw internal taxonomy terms without plain-language explanation. Use of `active_gates`/`unresolved_risk_flags`/`material_warnings`/`coverage_notices`/`conflicts` should be rendered as labeled, distinct visual categories (e.g., a green "supports," amber "unresolved," red "active gate" treatment), not a single undifferentiated list. Live status should be labeled explicitly (e.g., "Live analysis — computed just now") to distinguish from any future stored-snapshot view, since no snapshot view exists today (RCI is not persisted).

## Railway Activation and Rollback Plan (for future reference, not executed this sprint)

| Item | RCI (`RCI_LIVE_STOCK_ANALYSIS_ENABLED`) | Valuation Intelligence (`VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US`) |
|---|---|---|
| Exact variable name | `RCI_LIVE_STOCK_ANALYSIS_ENABLED` | `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`, `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US` |
| Expected value to enable | `1` | `1` (settable independently per market) |
| Redeploy required | Yes (env var read at process start via `os.getenv`, not hot-reloaded) | Yes, same reason |
| Rollback action | Remove the variable (or set to `0`) and redeploy | Same |
| Post-deploy checks | Confirm `/predict` response for a known symbol includes `recommendation_consolidation`; confirm signal/confidence unchanged | Confirm `ranking_alpha` unchanged on a Daily Picks dry run; confirm confidence shifts only within the documented +2/-4 asymmetric bound |
| Expected behavior when disabled | No `recommendation_consolidation` key present at all | No confidence adjustment from this engine |
| Expected behavior when enabled | Key present on every successful `/predict` response (cache-hit path) | Confidence shifts only when the AND cross-engine gate passes |
| Monitoring signals | RCI composition failure rate (via the existing `log.warning` line — needs a dashboard/alert, not yet wired) | Gate hit-rate (named in Epic 004's own Technical Debt Register as not yet wired into telemetry) |
| Stop/rollback criteria | Any unexpected change to `signal`/`confidence`/`composite_score` on a known test symbol; elevated error rate in logs | Any deviation in `ranking_alpha` from its pre-activation baseline; any confidence move outside the ±2/-4 documented bound |

## Decision Matrix

| Path | Decision Quality Impact | User Value | Operational Risk | Frontend Dependency | Valuation Dependency | Rollback Simplicity | Evidence Sufficiency | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A — Keep both disabled | None (no progress) | None (status quo) | None | None | None | N/A | N/A | Acceptable holding pattern only |
| B — Prepare RCI UI first | High | High once shipped | Low | High (new section, interface update) | Indirect (richer once Valuation is live) | Easy (flag stays off during design) | Sufficient | **Selected** |
| C — Enable RCI API only, no UI | Low-medium | None (no consumer) | Low-medium (untested in real traffic) | None | None | Easy | Sufficient if a test plan + telemetry is added first | Deferred, conditional |
| D — Revisit Valuation activation | High | High (unlocks RCI's strongest patterns) | Medium (live confidence-score influence) | None | N/A (it is the dependency) | Easy (kill switch) | Sufficient per Epic 004 closure | Recommended as a parallel, separate sprint |

## Required Final Decision

**Selected immediate next action: A. Begin RCI Live Stock Analysis UI / Consumer Design Sprint, while keeping all flags disabled.**

Justification: this path makes real forward progress (UI design), requires no production risk (flags stay off throughout), and does not require Valuation Intelligence's activation question to be resolved first — though Path D is explicitly recommended as a **separate, parallel-track sprint**, not deferred indefinitely, since it is the one concrete technical prerequisite (telemetry) standing between Epic 004's already-validated evidence and an activation decision.

**What happens immediately after this sprint:**
1. A UI/consumer design sprint for the "Evidence Summary" section (wireframes, copy, exact field-to-visual mapping, interface widening) — no flag changes.
2. In parallel, a short Valuation Intelligence Operational Activation Decision sprint (Path D) — wiring gate-hit-rate telemetry and making the explicit enable/no-enable call per market, informed by Epic 004's own closure recommendation, independent of RCI's UI timeline.

**What must remain disabled:** `RCI_LIVE_STOCK_ANALYSIS_ENABLED` and both `VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN`/`_US` — none are activated by this sprint or its immediate follow-ons until their respective design/telemetry prerequisites are independently complete.

**Evidence required before any Railway production variable is changed:**
- For RCI: a built, reviewed, and tested frontend section consuming the field; confirmation the section cannot be mistaken for a second recommendation (a design/copy review, not just code review).
- For Valuation Intelligence: gate-hit-rate telemetry wired into actual production monitoring, plus an explicit, documented per-market enable decision citing Epic 004's outcome-validation evidence directly (not re-derived).

## Deferred Paths and Reasons

- **Path C** deferred: no consumer exists yet to validate against; enabling it now would produce data with no defined success criterion. Revisit once a dedicated test plan and basic telemetry exist.
- **Path A** not selected as the final answer: it is safe but makes no progress on either open question this sprint was asked to resolve.

---

*No production code, frontend code, API response shape, Railway configuration, Daily Picks, Prediction Engine behavior, signal, confidence, score, engine grades, or gate behavior was modified by this sprint. No Valuation Intelligence or RCI flag was enabled. This document is the sprint's sole deliverable, alongside the roadmap/index updates listed below.*
