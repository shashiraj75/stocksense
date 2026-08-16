# Trade Postmortem — Evidence Completion Roadmap

Sprint 3b (feature/postmortem-explainability-phase). Describes FUTURE
phases only — nothing in this document is implemented by this sprint.
Companion to
[Trade-Postmortem-Evidence-Coverage-Matrix.md](Trade-Postmortem-Evidence-Coverage-Matrix.md),
whose `remediation_category` column (B/C/D/E) maps directly onto the
phases below.

## Phase A — Existing-data propagation across Buy call sites

> **Phase A1 IMPLEMENTED for FUTURE trades only** (Trade Postmortem
> Evidence Completion, Phase A1 —
> `feature/postmortem-evidence-phase-a-entry-propagation`, corrected
> 2026-08-07 after an owner audit; extended by the Daily Picks Phase A1
> Evidence-Gap Closure, 2026-08-10). Every live Buy entry point in the
> current product (Daily Picks Buy, Stock Detail recommendation Buy) now
> propagates the recommendation evidence it GENUINELY HAS AT BUY TIME into
> `paper_trade_entry_snapshot`, via `frontend/src/utils/entryEvidence.ts`
> and the corresponding `evidence_source`/`entry_evidence`/
> `idempotency_key` fields the backend Buy contract already supported. No
> backend production logic (scoring/ranking/selection) changed. This is
> NOT the same claim as "every field is always populated" — a field stays
> null whenever the underlying value was genuinely never computed:
> - `recommendation_signal` and `fundamental_score`: populated by BOTH the
>   Daily Pick Buy path (`Pick.price`/`Pick.fund_score`) and the Stock
>   Detail (RESEARCH) Buy path (`Prediction.signal`/
>   `Prediction.fundamental_score.score`) — A. AVAILABLE_NOW for new
>   trades on both paths.
> - `technical_signal` and `sentiment_score`: as of the 2026-08-10 Daily
>   Picks Phase A1 Evidence-Gap Closure, now populated by BOTH the Stock
>   Detail (RESEARCH) Buy path (`Prediction.technical.overall`/
>   `Prediction.sentiment_score.score`) AND the Daily Pick Buy path.
>   Investigation found both values already existed inside the Daily
>   Picks backend's own generation computation
>   (`_predict_stock()` in `backend/services/daily_picks.py`) — the SAME
>   `PredictionEngine.predict()` call the Research path uses, discarded
>   before publication rather than genuinely absent. `technical_signal`
>   (`result["technical"]["overall"]`, the same BUY/SELL/HOLD vocabulary)
>   and `sentiment_score` (`result["sentiment_score"]["score"]`, the
>   genuine numeric score, already carried internally for cross-sectional
>   ranking) are now additively exposed on the published `Pick` payload
>   and consumed verbatim by `buildEntryEvidenceFromDailyPick` — never
>   derived from `Pick.tech_score` (threshold conversion) or `Pick.sentiment`
>   (label-to-number mapping), which would have been fabrication. Both stay
>   null exactly when PredictionEngine genuinely didn't produce a value
>   (e.g. sentiment data unavailable) — see the corrected rows in
>   [Trade-Postmortem-Evidence-Coverage-Matrix.md](Trade-Postmortem-Evidence-Coverage-Matrix.md).
>   Daily Pick trades opened before this closure remain permanently null
>   for these two fields, same as any other pre-phase legacy trade.
> - A Daily Pick Buy whose horizon is switched away from the pick's
>   original horizon in the Buy modal now falls back to the Stock Detail
>   (RESEARCH) evidence builder for the horizon actually bought (a
>   Prediction is already fetched for the selected horizon in that flow),
>   rather than reusing the original horizon's stale evidence —
>   `evidence_source` intentionally stays `DAILY_PICK` in that case since
>   it still honestly describes where the user navigated from.
>
> Legacy trades opened before this change are NOT backfilled — their
> evidence remains permanently missing where it was never captured at
> entry. All captured evidence remains CLIENT_REPORTED (not
> SERVER_VERIFIED); there is still no server-authoritative recommendation
> identity. A genuine, product-reachable MANUAL Buy path (a Paper Trading
> Buy with no recommendation behind it) does not currently exist in the
> UI — only Daily Picks and Stock Detail Buy paths render the Buy modal
> today, so `MANUAL` remains the backend's safe default for any future
> caller that doesn't specify a source, not an exercised live path. Phases
> B/C/D/E below remain entirely unimplemented.

**Evidence produced:** `fundamental_score`, `sentiment_score`, and any
other `entry_snapshot.RecommendationContext` field that is already defined
in the schema but not populated by every code path that opens a paper
trade (manual Buy, daily-pick Buy, any future automated-entry path).

**Source authority:** the same fundamental/sentiment scoring services
already used by the daily pick pipeline — no new data source.

**Licensing:** none — internally computed scores, already licensed for use
elsewhere in the product.

**Point-in-time requirement:** the score must be captured at the moment of
trade entry, not recomputed after the fact — a post-hoc fundamental score
would silently misrepresent what was actually known at entry.

**Persistence model:** write directly into the existing
`paper_trade_entry_snapshot` row at trade-open time; no new table.

**Failure/retry behavior:** if the scoring service is unavailable at entry
time, the field stays `NULL` (current behavior) — this phase does not add
retries or backfill, since backfilling a point-in-time field after the
fact would violate the point-in-time requirement above.

**Privacy:** none — no new PII.

**Expected coverage improvement:** moves `signal_scorecard.fundamental_score`
and `signal_scorecard.sentiment_score` from B (EXISTING_DATA_NOT_PROPAGATED)
to A (AVAILABLE_NOW) for all NEW trades opened after each call site is
fixed.

**Risks:** low — this is a pure completeness fix to code paths that
already exist; the main risk is missing a call site during the audit.

**Migration implications:** none — additive field population only.

**Legacy-trade treatment:** trades opened before the fix stay `NULL`
permanently (E. LEGACY_NOT_RECOVERABLE for that specific field on that
specific trade) — never backfilled with a guessed value.

**Tests/rollout gates:** a test enumerating every Buy call site and
asserting each one populates `fundamental_score`/`sentiment_score` when
the underlying scoring service returns a value; canary on one call site
before rolling to all.

## Phase B — Durable server-verified recommendation identity/snapshot

**Evidence produced:** a durable, server-authoritative record of exactly
which recommendation (model version, signal values, timestamp) a given
Buy was actually placed against, independent of the client's report of it.

**Source authority:** the recommendation-generation service itself,
snapshotted at generation time rather than reconstructed later.

**Licensing:** none — internal system of record.

**Point-in-time requirement:** the snapshot must be taken at
recommendation-generation time, stored keyed to the recommendation ID, and
linked to the trade at entry — never reconstructed retroactively from
logs, which may have rotated or be incomplete.

**Persistence model:** a new durable table (e.g.
`recommendation_snapshot`) keyed by recommendation ID, referenced by
`paper_trade_entry_snapshot` via a foreign key rather than duplicating the
recommendation's fields.

**Failure/retry behavior:** if the snapshot write fails, the trade should
still open (this is an evidentiary enhancement, not a trading gate) but
the failure must be logged as a named evidence gap on that trade, not
silently dropped.

**Privacy:** none beyond what recommendation generation already stores.

**Expected coverage improvement:** strengthens `thesis_verdict` and
`signal_scorecard` claims from EVIDENCE_SUPPORTED toward
MECHANICALLY_VERIFIED for trades entered against a snapshotted
recommendation, and closes the "stable recommendation identity" gap noted
in the sprint brief's still-needed-confirmation list.

**Risks:** moderate — a new durable table with a new failure mode; must
not become a write that can block trade entry.

**Migration implications:** additive table + foreign key; no changes to
existing trade tables.

**Legacy-trade treatment:** trades entered before this phase permanently
lack a recommendation snapshot — E. LEGACY_NOT_RECOVERABLE for those
specific claims on those specific trades.

**Tests/rollout gates:** contract test that every new Buy either has a
recommendation snapshot or an explicit evidence gap recorded (never
silently neither); rollout behind a feature flag with a dry-run write
period before it affects claim evidence_class.

## Phase C — Later signal recapture (server-verified exit trigger timing)

**Evidence produced:** independent, server-side verification that an
auto-triggered close (`TARGET_HIT` / `STOP_LOSS`) actually happened when
and because the client reported — i.e. upgrading
`exit_trigger_timing_verification` from `CLIENT_REPORTED_UNVERIFIED` to
`SERVER_VERIFIED` for future trades.

**Source authority:** a server-side scheduler/monitor process that
independently observes price crossing the stop/target level, rather than
trusting only the client's close event.

**Licensing:** depends on the price-data source already licensed for
Phase E below; no new licensing category.

**Point-in-time requirement:** the server-side observation must be
timestamped at the moment of its own detection, and compared against the
client-reported close timestamp — a verification computed well after the
fact from daily bars is a materially weaker claim than a genuine
near-real-time server observation, and must not be presented as the same
evidence_class.

**Persistence model:** extend `paper_trade_exit_snapshot` with a
server-verification outcome field, written by the monitor process
independently of the client-driven close write path.

**Failure/retry behavior:** if server-side monitoring was unavailable
during the holding period (e.g. an outage), `exit_trigger_timing_verification`
stays `CLIENT_REPORTED_UNVERIFIED` — never silently upgraded.

**Privacy:** none.

**Expected coverage improvement:** moves `exit_evidence.exit_trigger_timing`
from C (REQUIRES_SERVER_VERIFICATION) to A for trades closed after this
phase ships.

**Risks:** the monitor process itself becomes a new source of truth that
must be at least as reliable as the client-reported path, or it will
introduce disagreements between client and server that surface as
CONFLICTING_EVIDENCE claims — which is an acceptable, honest outcome, but
must be handled explicitly rather than treated as a bug.

**Migration implications:** additive column; no backfill for historical
trades (see legacy treatment).

**Legacy-trade treatment:** trades closed before this phase permanently
show `CLIENT_REPORTED_UNVERIFIED` — E. LEGACY_NOT_RECOVERABLE, since no
independent server observation exists to recover.

**Tests/rollout gates:** a shadow-mode period where the monitor runs
without affecting evidence_class, comparing its conclusions against
client-reported closes to measure disagreement rate before it is trusted
to upgrade verification_level.

## Phase D — Contextual evidence acquisition (benchmark/sector/volatility/liquidity/macro/analyst/news)

**Evidence produced:** point-in-time benchmark index level, sector index
level, a volatility-regime indicator, a liquidity/volume indicator, and
(optionally, lower priority) macro/analyst-consensus/news context — each
captured at both entry and exit so contributor categories currently stuck
at CONTRIBUTOR_UNACQUIRED_EVIDENCE_001 (`STOCK_SELECTION`, `ENTRY_TIMING`,
`MARKET_CONDITIONS`, `SECTOR_CONDITIONS`, `VOLATILITY`, `LIQUIDITY`,
`NEWS_OR_EVENT`) and the benchmark-dependent contradiction factor
(`favourable_stock_movement_vs_adverse_benchmark_movement`) can move off
INSUFFICIENT_EVIDENCE.

**Source authority:** an approved external market-data provider for
benchmark/sector/volatility/liquidity; a licensed news/analyst-consensus
feed for the lower-priority items — never an unlicensed scrape.

**Licensing:** this is the phase's main gating cost — benchmark/sector
index data and any news/analyst feed require a commercial data license;
must be resolved before implementation starts, not discovered mid-build.

**Point-in-time requirement:** every acquired value must be captured (or
recoverable to) the trade's own entry/exit timestamps — a same-day
close-of-day value is a materially weaker claim than an intraday
point-in-time value and must be labeled as such, never conflated.

**Persistence model:** a new `contextual_market_evidence` table keyed by
(trade_id, timestamp, evidence_type), acquired asynchronously at trade
open/close and linked into the postmortem's evidence_items — kept
separate from `paper_trade_entry_snapshot`/`exit_snapshot` so a licensing
or provider change never touches the core trade schema.

**Failure/retry behavior:** bounded async retry with an explicit terminal
"acquisition failed" evidence gap recorded on the trade rather than an
indefinite retry loop; a provider outage must never block trade entry or
exit.

**Privacy:** none — market/sector data only, no PII.

**Expected coverage improvement:** the single largest coverage jump
available — 8 of the matrix's current 45 rows are
D. REQUIRES_NEW_ACQUISITION, all in this phase's scope.

**Risks:** licensing cost/availability is the primary risk; a secondary
risk is over-claiming precision (e.g. treating a same-day close value as
equivalent to an intraday point-in-time observation).

**Migration implications:** new table only; existing tables untouched.

**Legacy-trade treatment:** legacy trades can partially benefit if the
provider has sufficient historical depth to backfill point-in-time values
for their actual entry/exit timestamps — genuinely historically
recoverable, unlike Phase E's frozen legacy report path. Must be evaluated
provider-by-provider, not assumed.

**Tests/rollout gates:** per-evidence-type contract tests asserting every
acquired value carries its own point-in-time timestamp and source
attribution; staged rollout by evidence type (benchmark first, given it
unblocks a contradiction factor as well as a contributor category).

## Phase E — Price-path completeness (provider reliability, missing-session reconciliation, intraday vs. daily, corporate actions, server-verified trigger timing)

**Evidence produced:** complete, gap-free intraday price-bar coverage for
every trade's holding period, correctly reconciled across early-close
calendars, regular vs. extended sessions, and corporate actions (splits/
dividends), backing `governed_price_path`'s `target_touch`, `stop_touch`,
and `touch_order` factors for the full holding period rather than only
when bars happen to be available.

**Source authority:** the same approved external price-data provider(s)
already used for the existing `governed_price_path` module, extended with
a secondary/backup provider for gap-filling.

**Licensing:** existing provider license likely already covers this; a
backup provider for gap-filling would need its own license review.

**Point-in-time requirement:** bars must be attributable to the correct
session/timezone and adjustment basis (split/dividend-adjusted vs. raw) at
the time of the trade — an adjustment-basis mismatch is exactly the kind
of "server-verified but silently wrong" failure this system must never
produce.

**Persistence model:** extends the existing price-bar cache/store this
codebase already has for `governed_price_path`, adding a
missing-session-reconciliation pass and an explicit adjustment-basis field
per bar range.

**Failure/retry behavior:** bounded retry against the primary provider,
then bounded retry against a secondary provider if configured, then an
explicit `GOVERNED_TOUCH_NO_BARS`/`INCOMPATIBLE_BASIS` conclusion — never
an indefinite retry, and never a silent basis substitution.

**Privacy:** none.

**Expected coverage improvement:** moves `governed_price_path.target_touch`
/`stop_touch`/`touch_order` from C (REQUIRES_SERVER_VERIFICATION, i.e.
already server-sourced but incomplete) closer to full A-level coverage as
gap rates fall; also unblocks the price-path-dependent contradiction
factors already at A but currently reliant on partial coverage.

**Risks:** corporate-action reconciliation is the highest-complexity part
of this phase — an incorrectly adjusted bar range could produce a
confidently wrong "target touched" claim, which is worse than the current
honest INSUFFICIENT_EVIDENCE. Extensive golden-data testing is required
before trusting this path to upgrade verification_level.

**Migration implications:** none to existing trade tables; price-bar cache
schema gains an adjustment-basis column.

**Legacy-trade treatment:** legacy (1.1.0) `price_path` reports stay
frozen and are explicitly out of scope (E. LEGACY_NOT_RECOVERABLE) per the
"no dual semantic authority" gate already established in this codebase —
this phase only ever improves the current (1.2.0) `governed_price_path`
authority.

**Tests/rollout gates:** golden-dataset tests covering at minimum a split,
a dividend, an early-close session, and an extended-hours-only fill,
before this phase is allowed to change any claim's evidence_class from
INSUFFICIENT_EVIDENCE to a positive class.

## Candidate future F. DELIBERATELY_NOT_ASSESSABLE limits on phases D/B above

**Reconciliation note (completion pass):** the current 45-row coverage
matrix classifies **zero** factors as F. DELIBERATELY_NOT_ASSESSABLE
(`F=0` is correct today — verified against the actual rule registry;
`sum(A..F) == 45` holds). The two items below are NOT members of the
current governed inventory's F category; today's code
(`evidence_attribution.py`'s `_UNACQUIRED_CATEGORIES` tuple) treats both
identically to the other acquisition-gated contributor categories
(`STOCK_SELECTION`, `MARKET_CONDITIONS`, etc.) — hence their current
classification as D. REQUIRES_NEW_ACQUISITION (`PRICE_NOISE`) and
B. EXISTING_DATA_NOT_PROPAGATED (`ADMINISTRATIVE_ACTION`) in the matrix,
matching what the code actually does today. This section is a
forward-looking caveat about what Phases B/D should NOT promise to fully
close, not a claim about present-day miscategorization:

- **`contributor_assessments.PRICE_NOISE`** — even after Phase D's
  benchmark/sector/volatility acquisition, this factor should likely
  remain classified D-with-a-documented-ceiling rather than ever flip to
  a positive evidence_class with high confidence: price noise is
  genuinely not cleanly distinguishable from a real signal without a
  materially different statistical model than anything Phase D adds.
  Phase D should close the *acquisition* gap (the data becomes
  available) without overstating what conclusion that data can safely
  support.
- **`contributor_assessments.ADMINISTRATIVE_ACTION`** — corporate-action
  attribution (e.g. "this trade's outcome was driven by a stock split")
  is a fundamentally different attribution question than the other
  contributor categories. Phase E's corporate-action handling is about
  price-bar *correctness* (adjusting MFE/MAE/touch calculations for
  splits/dividends), not about attributing outcome causation to the
  corporate action itself — Phase B propagating the existing data should
  not be read as a promise that this becomes a confidently-assessed
  contributor category.

Every other gap in the current coverage matrix is categorized B/C/D
(propagation, verification, or acquisition) with no such ceiling caveat —
a concrete, named phase above is expected to fully close it.
