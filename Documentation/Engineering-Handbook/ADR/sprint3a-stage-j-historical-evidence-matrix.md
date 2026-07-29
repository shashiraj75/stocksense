# Trade Postmortem Sprint 3A — Stage J Formal Historical Evidence Matrix

Governs `services.postmortem.price_path_eligibility` (trade-context decision,
Stage J1A) and `services.postmortem.price_path_evidence_decision` (evidence
decision, Stage J1B). Every row below is either implemented, unit-tested, or
explicitly flagged as remaining scope in the Stage J checkpoint report — this
document is not aspirational; it is the source of truth those modules and
their tests must match.

Columns: **Trade** = trade-record validity · **Sym** = symbol validity ·
**EntrySnap/ExitSnap** = snapshot status · **Evidence** = compatible
persisted evidence present · **Replay/Acq/Calc** = permitted? ·
**Ceiling** = report_completeness_ceiling · **Provider** = provider-call
expectation · **Outbox** = outbox outcome · **Report** = report outcome ·
**Fallback** = exact user-facing text where evidence is insufficient.

| # | Trade | Sym | EntrySnap | ExitSnap | Evidence | Replay | Acq | Calc | Ceiling | Provider | Outbox | Report | Reason codes | Fallback |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | valid | valid | PRESENT_VALID | PRESENT_VALID | none | n/a | yes | yes | COMPLETE | called | GENERATING→COMPLETE | new price-path report | — | — |
| 2 | valid | valid | PRESENT_VALID | PRESENT_VALID | none, provider empty | n/a | yes | no | LIMITED_EVIDENCE | called, empty | FAILED_RETRYABLE (SOURCE_UNAVAILABLE, not terminal) | none this attempt | SOURCE_UNAVAILABLE | "Insufficient evidence to determine this factor reliably." |
| 3 | valid | valid | MISSING | PRESENT_VALID | none | n/a | yes | yes | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report, entry-thesis fields null | MISSING_ENTRY_CONTEXT | as above, entry-thesis fields only |
| 4 | valid | valid | PRESENT_INVALID | PRESENT_VALID | none | n/a | yes | yes | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report, entry-thesis fields null, explicit limitation | ENTRY_CONTEXT_INVALID | as above |
| 5 | valid | valid | PRESENT_VALID | MISSING | none | n/a | yes | yes | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report, exit-rationale fields null | MISSING_EXIT_CONTEXT | as above |
| 6 | valid | valid | PRESENT_VALID | PRESENT_INVALID | none | n/a | yes | yes | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report, exit-rationale fields null, explicit limitation | EXIT_CONTEXT_INVALID | as above |
| 7 | valid | valid | MISSING | MISSING | none | n/a | yes | yes | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report, both context blocks null | MISSING_ENTRY_CONTEXT, MISSING_EXIT_CONTEXT | as above |
| 8 | invalid (opened_at NULL) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_TIMELINE | not applicable — acquisition never attempted |
| 9 | invalid (closed_at NULL) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_TIMELINE | as above |
| 10 | invalid (opened_at tz-naive) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_TIMELINE | as above |
| 11 | invalid (closed_at tz-naive) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_TIMELINE | as above |
| 12 | invalid (closed < opened) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_TIMELINE | as above |
| 13 | valid, same-instant (closed == opened) | valid | PRESENT_VALID | PRESENT_VALID | none | n/a | yes | yes | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | AMBIGUOUS_RESOLUTION report | AMBIGUOUS_RESOLUTION | "Insufficient evidence to determine this factor reliably." (touch order) |
| 14 | invalid (entry_price NULL) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_PRICE | not applicable |
| 15 | valid, exit_price NULL | valid | PRESENT_VALID | PRESENT_VALID | none | n/a | yes | yes (partial) | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report; MFE/signed-MAE/MAE-magnitude present, captured_mfe/giveback null | MISSING_EXIT_PRICE | as above, exit-dependent fields only |
| 16 | invalid (entry_price <= 0 / NaN / inf) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_PRICE | not applicable |
| 17 | invalid (exit_price present, non-finite) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_TRADE_PRICE | not applicable |
| 18 | valid | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_MARKET | not applicable |
| 19 | valid, symbol="" | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_SYMBOL (Stage J1B) | not applicable |
| 20 | valid, symbol malformed (e.g. control chars) | — | — | — | — | no | no | no | LIMITED_EVIDENCE | never | no row created | none | INVALID_SYMBOL | not applicable |
| 21 | valid | valid | PRESENT_VALID | PRESENT_VALID | none, provider returns `[]` | n/a | yes | no | LIMITED_EVIDENCE | called, empty | FAILED_RETRYABLE (SOURCE_UNAVAILABLE) | none this attempt | SOURCE_UNAVAILABLE | as row 2 — never treated as proof the trade/symbol was invalid |
| 22 | valid | valid | PRESENT_VALID | PRESENT_VALID | none, provider rows fail schema validation | n/a | yes | no | LIMITED_EVIDENCE | called, rejected | FAILED_RETRYABLE (SOURCE_INVALID) | none, no partial evidence persisted | SOURCE_INVALID | as row 2 |
| 23 | valid | valid | PRESENT_VALID | PRESENT_VALID | compatible | yes | no | yes | inherits from context | not called | GENERATING→settled from replay | report from persisted evidence, deterministic hash match | COMPATIBLE_REPLAY | — |
| 24 | valid | valid | PRESENT_VALID | PRESENT_VALID | incompatible (different window/basis/version) | no | yes | yes | inherits from context | called | new versioned outbox row | new report, prior evidence untouched, immutable | ACQUISITION_REQUIRED | — |
| 25 | valid | valid | PRESENT_VALID | PRESENT_VALID | evidence row exists for a DIFFERENT user_id | no (cross-user rows never match) | yes | yes | inherits from context | called | new outbox row for this user | new report, other user's row never read/exposed | ACQUISITION_REQUIRED | — |
| 26 | valid | valid | PRESENT_VALID | PRESENT_VALID | Sprint 2 report exists (schema 1.0.0), no price-path evidence | n/a | yes | yes | inherits from context | called | new price-path (1.1.0) outbox row | new price-path report; Sprint 2 report untouched | — | — |
| 27 | valid | valid | PRESENT_VALID | PRESENT_VALID | price-path report already exists for this exact version triple | n/a | no | no | prior report's own ceiling | not called | none — idempotent | existing report returned verbatim | — (PRICE_PATH_ALREADY_COMPLETE) | — |
| 28 | valid | valid | — | — | — | n/a | yes (retry) | yes | inherits from context | called (retry) | FAILED_RETRYABLE row reclaimed after backoff | pending until this attempt settles | — | — |
| 29 | valid | valid | — | — | — | n/a | no | no | LIMITED_EVIDENCE | never (attempt limit exceeded) | FAILED_TERMINAL, MAX_ATTEMPTS_EXCEEDED | none, never retried again | — | not applicable — durable terminal failure |
| 30 | valid | valid | PRESENT_VALID | PRESENT_VALID | evidence acquired, adjustment_basis unrecognized | n/a | yes | no (MFE/MAE) | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | report, excursion fields null | PRICE_BASIS_INCOMPATIBLE | "Insufficient evidence to determine this factor reliably." |
| 31 | valid | valid | PRESENT_VALID | PRESENT_VALID | split event inside window, reconciled deterministically | n/a | yes | yes | per basis outcome | called | GENERATING→settled | report notes split-adjusted basis | — | — |
| 32 | valid | valid | PRESENT_VALID | PRESENT_VALID | no corporate-action metadata returned | n/a | yes | yes (no action assumed FALSE, not proven) | inherits from context | called | GENERATING→settled | report never claims "no corporate action occurred" | — | never asserted as proof; absence of metadata ≠ absence of event |
| 33 | valid, entry==exit calendar session, partial session | valid | PRESENT_VALID | PRESENT_VALID | none | n/a | yes | yes (MFE/MAE only) | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | touch/order fields AMBIGUOUS_RESOLUTION | AMBIGUOUS_RESOLUTION | as row 13 |
| 34 | valid | valid | PRESENT_VALID | PRESENT_VALID | evidence acquired, zero complete interior bars | n/a | yes | no (touch) | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | MFE/MAE from boundary only, touch INSUFFICIENT_EVIDENCE | LEVEL_HISTORY_INCOMPLETE (via price_path_claims) | as row 30 |
| 35 | valid | valid | PRESENT_VALID | PRESENT_VALID | stop and target both touched same bar | n/a | yes | yes (MFE/MAE), no (order) | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | touch claim BOTH_SAME_BAR_AMBIGUOUS (CONFLICTING_EVIDENCE) | BOTH_SAME_BAR_AMBIGUOUS | "Insufficient evidence to determine this factor reliably." (which level hit first) |
| 36 | valid | valid | PRESENT_VALID | PRESENT_VALID | level (stop/target) changed mid-trade, history incomplete | n/a | yes | yes (MFE/MAE), no (order) | LIMITED_EVIDENCE | called | GENERATING→LIMITED_EVIDENCE | touch claim LEVEL_HISTORY_INCOMPLETE (INSUFFICIENT_EVIDENCE); final levels never applied retrospectively across the whole trade | LEVEL_HISTORY_INCOMPLETE | as row 30 |

## Implementation status against this matrix (honest, as of this Stage J pass)

- **Rows 1, 3–7, 8–12, 14, 16–18, 19–20 (Stage J4 symbol validation, new), 15, 26, 27, 29** — implemented and covered
  (unit + regression + a subset newly added to real-PG, see the Stage J final
  report's traceability table).
- **Rows 21–22 (SOURCE_UNAVAILABLE vs SOURCE_INVALID as distinct, named
  states)** — implemented in `price_path_evidence_decision.py` (Stage J1B,
  this pass). Previously these both fell through to the generic
  `FAILED_RETRYABLE` path with no named distinction at the eligibility layer.
- **Rows 23–25, 28 (compatible replay, cross-user isolation, retry
  reclaim)** — replay behavior (row 23/24) already existed from Sprint 3A's
  earlier acquisition-flow work and is unit/regression tested; this pass adds
  the explicit `COMPATIBLE_REPLAY` / `ACQUISITION_REQUIRED` naming at the
  decision-model layer so the distinction is a typed fact, not an inferred
  side effect of "evidence lookup returned non-None."
- **Rows 30, 32, 33, 35, 36 (basis/ambiguity ceiling integration)** —
  downstream calculation-layer behavior (price_path_calculator.py,
  price_path_claims.py) already enforces these; this pass wires
  `PRICE_BASIS_INCOMPATIBLE` and `AMBIGUOUS_RESOLUTION` into the typed
  evidence decision so the eligibility layer can name them, but the full
  ceiling-composition function described in Stage J3 (a single function
  folding in basis/boundary/level-history ceilings, not just
  snapshot/price) is **not** completed in this pass — flagged as remaining
  scope.
- **Row 31 (split reconciliation)** — pre-existing `price_path_acquisition`
  adjustment-basis handling; no NEW deterministic reconciliation logic was
  added or verified against a real split event in this pass. Flagged as
  remaining scope.
- **Row 34** — pre-existing `LEVEL_HISTORY_INCOMPLETE` claim behavior,
  unit-tested; not newly re-verified against a zero-interior-bars real
  historical trade in this pass.

No row in this matrix is coded around with a fabricated value. Every
LIMITED_EVIDENCE/INSUFFICIENT_EVIDENCE path renders the fixed fallback text
and never a zero, a guessed price, or an inferred market event.

## Stage J1B load-bearing integration (this pass)

The gap explicitly flagged in the prior checkpoint — "J1B is unit-tested but
bypassed by the live generation path" — is closed for the decision points
that have a real, grounded live signal:

- **Report replay vs evidence replay** are now distinct, both proven:
  report replay (`existing_pp_report is not None`, `paper_trading.py`) never
  reaches the outbox claim at all; evidence replay (compatible evidence,
  no report yet) is governed by `classify_replay_or_acquisition`, proven by
  `tests/postgres_integration/test_price_path_historical_compatibility.py::
  TestTrueEvidenceReplayWithoutAnyReport` — a compatible evidence bundle is
  persisted directly (bypassing `/sell`/`/generate`), then `/generate` is
  called with every provider function raising `AssertionError`, and still
  succeeds by constructing a report from the persisted evidence alone.
- **Provider-error-code mapping**: every `PriceProviderAcquisitionError`
  code is classified via `classify_provider_failure` before the existing
  `mark_retryable_failure` call — `PROVIDER_UNEXPECTED_COLUMN_SHAPE` →
  `SOURCE_INVALID`; `PROVIDER_FETCH_FAILED`/`PROVIDER_RESPONSE_TOO_LARGE` →
  `SOURCE_UNAVAILABLE`. Both currently settle `FAILED_RETRYABLE` (not
  terminal) — a deliberate choice: the outbox's own
  `MAX_ATTEMPTS_BEFORE_TERMINAL` already bounds a permanently-broken
  provider contract to a finite number of attempts, so escalating
  `SOURCE_INVALID` straight to `FAILED_TERMINAL` here would remove that
  existing safety margin without a corresponding benefit.
- **Acquired-evidence classification** (`classify_acquired_evidence`) is
  now derived directly from `PricePathEvidenceBundle.data_completeness` —
  the REAL, already-computed 5-value enum
  (`COMPLETE`/`PARTIAL`/`UNAVAILABLE`/`INVALID_SOURCE_DATA`/
  `AMBIGUOUS_RESOLUTION`) `build_price_path_evidence` produces from real
  facts (empty provider response, malformed rows, a split inside the
  window). An earlier draft of this function took separately-invented
  `adjustment_basis_known`/`ambiguous_resolution` booleans with no grounded
  live source; rewritten to derive from the real field instead. Proven live
  by `tests/regression/test_paper_trading_price_path_lease_lifecycle.py::
  TestSuccessfulGenerationAndIdempotentReplay::
  test_split_in_window_is_classified_ambiguous_and_caps_limited_evidence` —
  a real split-in-window response (via the REAL, unfaked
  `build_price_path_evidence`) flows through to `AMBIGUOUS_RESOLUTION` and
  caps the persisted report at `LIMITED_EVIDENCE`.
- **`PRICE_BASIS_INCOMPATIBLE` has no live caller today** — honestly
  documented, not forced: the acquisition layer's real 5-value enum does
  not currently distinguish "bars available but basis unknown" from
  "ambiguous" (a split zeroes `bars_observed` AND sets
  `AMBIGUOUS_RESOLUTION`, never a separate basis-only state). The state
  remains declared per Stage J7's requirement, for a future acquisition-
  layer signal.
- **Evidence ceiling composition**: `persist_price_path_report` now composes
  `prior_report.status`, `payload.status`, `trade_context_ceiling` (J1A),
  AND `evidence_decision.report_completeness_ceiling` (J1B) through the
  single `compute_report_completeness_ceiling` function — proven by
  `tests/unit/test_price_path_generation.py`'s
  `test_trade_context_ceiling_alone_caps_an_otherwise_complete_outcome` and
  the live split-in-window regression test above.
- **Decision provenance**: `evidence_status`, `calculation_status`,
  `provider_call_expected`, and `reason_codes` are persisted into
  `structured_report["price_path"]["evidence_decision"]`; `limitations` are
  folded into the existing `evidence_gaps` list. No raw provider payload is
  added.
- **Report/outbox consistency**: unchanged and already correct —
  `mark_terminal` was already called with `report.status` (the final
  composed value), so this was structurally guaranteed before this pass
  too; the true-evidence-replay test explicitly re-asserts it.

### Remaining Stage J closure work (still open after this pass)

- Complete persisted source-manifest fields (normalization version, source
  scope, provider/library version as named, queryable report fields).
- Explicit historical-window contamination traceability (dedicated Stage-J-
  labeled tests for entry/exit boundary inclusion, weekend/holiday
  exclusion, DST, timezone-naive handling — the underlying logic is correct
  from earlier Sprint 3A work but not re-proven under a Stage J test name).
- `AMBIGUOUS_RESOLUTION`'s same-day/boundary-ambiguity trigger (as opposed
  to the split-in-window trigger, which is now wired) has no live signal
  yet at this decision point.
- Complete requirement-to-test traceability table (the full 40-scenario
  matrix from the original Stage J request).
- Remaining Stage J10 real-PG scenarios: invalid exit snapshot with an
  explicit limitation, atomic limited-report+terminal-outbox settlement as
  its own dedicated named test (currently proven only implicitly via the
  evidence-replay test's own outbox/report status equality assertion).
