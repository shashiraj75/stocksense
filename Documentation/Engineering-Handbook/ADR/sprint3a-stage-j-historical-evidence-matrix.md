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

## Stage J1B-Fail-Closed-Hardening (this pass)

Corrects six confirmed defects in the prior pass's J1B integration:

1. **Unknown data_completeness fell through to COMPLETE.** Fixed:
   `classify_acquired_evidence` now checks the known-value dict FIRST and
   returns a new `UNSUPPORTED_EVIDENCE_COMPLETENESS` status
   (`calculation_status=CALCULATION_UNAVAILABLE`,
   `persistence_permitted=False`, ceiling `LIMITED_EVIDENCE`) for anything
   not in the exhaustive known set — `None`, `""`, a typo, a future value.
   Exhaustively parameterized tests in `test_price_path_evidence_decision.py::
   TestFailClosedOnUnsupportedCompleteness`.

2. **Acquisition provenance and evidence quality were one conflated
   object**, so a replayed report's `provider_call_expected` could read
   `True`. Split into two separate dataclasses:
   `HistoricalEvidenceAcquisitionDecision` (WHERE evidence came from —
   `acquisition_status`, `provider_call_expected`,
   `compatible_evidence_found`, `evidence_id`) and
   `HistoricalEvidenceQualityDecision` (WHETHER it's usable —
   `evidence_status`, `calculation_status`, `persistence_permitted`,
   `report_completeness_ceiling`). Acquisition is decided ONCE, before
   anything about quality is known, and never revised afterward. Both are
   persisted as separate `structured_report["price_path"]` blocks —
   `acquisition_decision` and `evidence_quality_decision`.

3. **`calculation_status` didn't gate calculator invocation** —
   `build_price_path_report_payload` (which calls `compute_excursion`/
   `detect_touches`/`classify_touch_order`) ran regardless. Fixed: the
   live path now branches on `quality_decision.calculation_status` —
   `CALCULATION_ELIGIBLE` calls the real payload builder;
   `CALCULATION_UNAVAILABLE` calls a new `build_unavailable_report_payload`
   that never touches the calculator and sets every analytic field to
   `None` explicitly (never a fabricated zero).

4. **Fresh bundles were persisted before quality classification**, so
   `SOURCE_INVALID` couldn't prevent persistence. Fixed: the live path now
   classifies (`classify_acquired_evidence`) BEFORE the
   `persist_price_path_evidence` call, and only persists when
   `quality_decision.persistence_permitted` is `True`. Decided explicitly
   per state: `SOURCE_UNAVAILABLE` (zero-bar, honest manifest, never
   fabricated bars) → persistence permitted; `SOURCE_INVALID` and
   `UNSUPPORTED_EVIDENCE_COMPLETENESS` → never persisted.

5. **`classify_provider_failure`'s result only reached a log line** — the
   actual `mark_retryable_failure` call was unconditional and hard-coded
   right next to it. Fixed: a new typed `ProviderFailurePolicy` (per error
   code: `retry_permitted`, `terminal_permitted`,
   `evidence_persistence_permitted`, `report_permitted`,
   `sanitized_error_code`) via `get_provider_failure_policy` is now what
   the live path branches on to choose `mark_retryable_failure` vs
   `mark_terminal_failure`. All three known codes currently resolve
   `retry_permitted=True` (unchanged observable behavior — the outbox's
   own attempt-limit already bounds a permanent failure), but the OUTCOME
   now genuinely comes from the policy, not a separate hard-coded action.
   An unrecognized code fails closed via a documented default policy
   (never permits persistence or a report).

6. **`PRICE_BASIS_INCOMPATIBLE` had no grounded live signal.** Per Stage
   6's own instruction ("if no grounded distinction is currently
   possible, remove from the active runtime enum, retain only as
   documented future scope"): removed as a value any live function can
   return. Kept as `PRICE_BASIS_INCOMPATIBLE_FUTURE_SCOPE`, documented,
   unreferenced by any branch — a future acquisition-layer signal
   (a persisted, provider-derived basis-compatibility field) can adopt it
   without a rename. Unknown/incompatible basis today still fails closed
   through `AMBIGUOUS_RESOLUTION` (the split-in-window case) or
   `UNSUPPORTED_EVIDENCE_COMPLETENESS` (anything genuinely unrecognized) —
   never silently `COMPLETE`.

### Still open after this pass

- `AMBIGUOUS_RESOLUTION`'s same-day/boundary-ambiguity trigger (as
  opposed to the split-in-window trigger, which is wired) has no live
  signal yet.
- Source-manifest field persistence (normalization version, source scope
  as named report fields).
- Explicit window-contamination traceability under a Stage J test name.
- Full 40-scenario requirement-to-test traceability table.
- Remaining Stage J10 real-PG scenarios not yet added this pass:
  malformed-provider-response policy-driven outcome as its own dedicated
  test (currently only unit-tested via `ProviderFailurePolicy`), invalid
  exit snapshot with an explicit limitation.

## Stage J1B-Assurance-Closure (this pass)

1. **Provider-failure governance consolidated.** `classify_provider_failure`
   was fully dead code — defined, unit-tested, but never called by the live
   path (`get_provider_failure_policy` was already the sole authority in
   `_attempt_price_path_enhancement`, from the prior pass). Removed the
   function and its `_SOURCE_INVALID_PROVIDER_CODES` table entirely rather
   than leaving a second, unconsulted classification. `get_provider_failure_
   policy`/`ProviderFailurePolicy` is now documented in the module header as
   the sole provider-failure authority.

2. **`compute_report_completeness_ceiling` fail-open bug fixed.** The prior
   implementation (`"LIMITED_EVIDENCE" if "LIMITED_EVIDENCE" in ceilings
   else "COMPLETE"`) treated ANY value that wasn't literally the string
   `"LIMITED_EVIDENCE"` as equivalent to `"COMPLETE"` — including `None`,
   `""`, a typo, or a genuinely unknown future ceiling value. Now validates
   every input against the known two-value set and fails closed to
   `LIMITED_EVIDENCE` on anything unrecognized. Exhaustively parameterized
   tests (`TestCeilingComposerFailsClosedOnUnknownInputs`).

3. **`persistence_permitted` renamed `fresh_persistence_permitted`**
   throughout (`HistoricalEvidenceQualityDecision`, the live path, all
   tests) — the field only ever governs whether a FRESH bundle may be
   inserted; on a replay path the evidence row already exists and is never
   affected by this field. `ProviderFailurePolicy`'s own persistence field
   is separately named `evidence_fresh_persistence_permitted` for the same
   reason.

4. **Calculator gating proven with direct instrumentation**, not inferred
   from null output fields — `test_zero_bars_never_invokes_the_calculator`
   patches `compute_excursion`/`detect_touches`/`classify_touch_order`
   directly and asserts zero calls for a `SOURCE_UNAVAILABLE` outcome, with
   `test_complete_evidence_does_invoke_the_calculator` as the positive
   control proving the spy mechanism itself is sound.

5. **New real-PG scenario**: `TestMalformedProviderResponseUsesPolicyDrivenOutcome`
   — `PROVIDER_UNEXPECTED_COLUMN_SHAPE` remains distinguishable from
   `PROVIDER_FETCH_FAILED` in the durable outbox error code, zero evidence
   persisted, zero report created.

### Assurance traceability (partial — see Known Limitations)

| Requirement | Production path | Test | Type | Result |
|---|---|---|---|---|
| Unknown data_completeness fails closed | `classify_acquired_evidence` | `TestFailClosedOnUnsupportedCompleteness` (9 params) | unit | pass |
| Ceiling composer fails closed | `compute_report_completeness_ceiling` | `TestCeilingComposerFailsClosedOnUnknownInputs` | unit | pass |
| Provider-failure policy is sole authority | `get_provider_failure_policy` | `TestProviderFailurePolicy` | unit | pass |
| Calculator never called when unavailable | `_attempt_price_path_enhancement` | `test_zero_bars_never_invokes_the_calculator` | regression (spy) | pass |
| Calculator called when eligible (control) | same | `test_complete_evidence_does_invoke_the_calculator` | regression (spy) | pass |
| Fresh acquisition provenance | same | `test_split_in_window_is_classified_ambiguous_and_caps_limited_evidence` | regression | pass |
| Evidence replay provenance (provider_call_expected=false) | same | `TestTrueEvidenceReplayWithoutAnyReport` | real-PG | pass |
| Zero-bar report never fabricates analytics | same | `TestZeroBarResultNeverFabricatesAnalytics` | real-PG | pass |
| Malformed provider response policy outcome | same | `TestMalformedProviderResponseUsesPolicyDrivenOutcome` | real-PG | pass |
| Transient provider failure policy outcome | same | `TestTransientProviderFailureMarksRetryable` | real-PG | pass |

### Known limitations after this pass (honest, not exhaustive)

Given the bounded scope of this phase, several Stage 5 scenarios from the
requesting checkpoint were **not** added as dedicated real-PG tests this
pass — their production behavior is correct and covered indirectly by
existing tests, but not under a dedicated named real-PG scenario:
unsupported-completeness fail-closed through the real endpoint (unit-level
only), invalid-source non-persistence through the real endpoint (unit-level
only), atomic limited-settlement under a forced stale-claimant/rollback
condition (structurally guaranteed by the existing `conn.transaction()`
wrapping, not independently re-proven this pass), and full trade/snapshot
immutability across all three paths (proven for one path in the prior
pass). Aggregate real-PG collection increased by 1 (144→145) this pass —
a single new named scenario, not a claim of covering the full Stage 5
matrix.

## Stage J1B-Real-PG-Assurance-Completion (this pass)

1. **ProviderFailurePolicy simplified** — `retry_permitted`/`terminal_permitted`
   (an ambiguous combination that described every current code identically
   and left the live path to independently decide which applied) replaced
   with a single `immediate_outbox_outcome` field
   (`IMMEDIATE_FAILED_RETRYABLE` | `IMMEDIATE_FAILED_TERMINAL`). The live
   path now branches directly on this field. The outbox's own attempt-limit
   mechanism (`outbox.py`, unchanged, already tested) remains the sole path
   from a retryable failure to an eventual terminal one.

2. **Bare `assert evidence is not None` replaced** with an explicit
   fail-closed branch: a `COMPATIBLE_REPLAY` decision with no evidence
   object present now settles `FAILED_RETRYABLE` under a sanitized
   `INTERNAL_INTEGRITY_VIOLATION` code, never a crash and never bypassable
   under optimized (`python -O`) execution. Noted honestly: given the
   current code shape (`evidence = ctx.compatible_evidence`, the same
   object reference used to decide `compatible_evidence_found`), this
   specific contradiction is structurally unreachable today — the fix is
   defensive-in-depth against a future refactor that decouples the two,
   verified by code review rather than a forced test.

3. **Formal invalid-bundle report policy implemented**: `SOURCE_INVALID`
   and `UNSUPPORTED_EVIDENCE_COMPLETENESS` now produce **no report at
   all** (not even LIMITED_EVIDENCE) — the outbox settles
   `FAILED_RETRYABLE` under the evidence status as its own sanitized error
   code, applied identically whether the bad classification came from a
   fresh bundle or a replay of previously-persisted (e.g. corrupted)
   evidence. `SOURCE_UNAVAILABLE` remains the one state that may still
   produce an honest `LIMITED_EVIDENCE` report from its zero-bar manifest.
   This closes a real behavioral gap: before this pass, the live path
   built and persisted a `LIMITED_EVIDENCE` report even for
   `SOURCE_INVALID`/`UNSUPPORTED` evidence.

4. **`INVALID_SOURCE_DATA` confirmed unreachable via genuine acquisition** —
   `price_path_acquisition.build_price_path_evidence` has no code path
   that ever assigns `STATUS_INVALID_SOURCE_DATA` (verified by direct
   grep/code inspection). The only real service-boundary way to exercise
   `UNSUPPORTED_EVIDENCE_COMPLETENESS`'s fail-closed behavior is a
   persisted evidence row whose `data_completeness` has been corrupted
   (simulating legacy/damaged data) — implemented as
   `TestUnsupportedCompletenessFailsClosedThroughRealReplay`.

### Real-PG collection: 151 (up from 145), 6 new dedicated scenarios

| # | Scenario | Test |
|---|---|---|
| 5A | Fresh acquisition provenance | `TestFreshAcquisitionProvenanceThroughRealEndpoint` |
| 5B | Unsupported completeness fail-closed (corrupted persisted data) | `TestUnsupportedCompletenessFailsClosedThroughRealReplay` |
| 5D (strengthened) | Malformed provider response, policy-driven | `TestMalformedProviderResponseUsesPolicyDrivenOutcome` |
| 5F | Atomic settlement / forced stale-claimant rollback | `test_stale_claimant_report_insert_rolls_back_atomically` (test_price_path_generation.py) |
| 5G | Replay immutability, byte-for-byte row comparison | `TestReplayImmutabilityByteForByte` |
| 5H | Trade/snapshot immutability, source-unavailable path | `TestTradeAndSnapshotImmutabilityAcrossPaths` |
| 5I | Report replay separation, no new provenance fabricated | `TestReportReplaySeparationThroughRealEndpoint` |

### Still not implementable / still open

- **5C (invalid-source through real bundle validation)**: genuinely not
  implementable without adding new production validation logic that
  doesn't exist today — `INVALID_SOURCE_DATA` has zero live producers.
  The exception-based `SOURCE_INVALID` trigger (5D,
  `PROVIDER_UNEXPECTED_COLUMN_SHAPE`) remains the only real path to that
  evidence status.
- Calculator-gating matrix is not exhaustive: `SOURCE_UNAVAILABLE` is
  directly spied (prior pass); `SOURCE_INVALID`/`UNSUPPORTED_EVIDENCE_
  COMPLETENESS` are now structurally guaranteed to never reach the
  calculator (Stage 3's no-report policy means `build_price_path_report_
  payload`/`build_unavailable_report_payload` are never even called for
  these two states — verified by code review, not a dedicated spy test
  this pass).
- Source-manifest field persistence, explicit window-contamination
  traceability, and the full 40-scenario table remain out of scope for
  this bounded J1B phase, as before.

## Stage J1B-Final-Reconciliation (this pass)

Corrects two internal contradictions in the prior pass's own claims:

1. **`ProviderFailurePolicy` reduced to exactly three load-bearing
   fields**: `evidence_status`, `immediate_outbox_outcome`,
   `sanitized_error_code`. The prior pass claimed "every field here is
   read and enforced by the live path" while `evidence_fresh_
   persistence_permitted`, `report_permitted`, and `limitations` were
   never actually read anywhere — a caught `PriceProviderAcquisitionError`
   has, by construction, no validated bundle at all for EVERY current
   code, so "no evidence, no report" is a property of the exception path
   itself, not a per-code policy decision worth its own fields.
   `evidence_status` drives sanitized **logging** only (a `log.warning`
   call); it does **not** drive `last_error_code` — `sanitized_error_code`
   alone is what `mark_retryable_failure`/`mark_terminal_failure` persist.

2. **`INVALID_SOURCE_DATA` removed from `_DATA_COMPLETENESS_TO_EVIDENCE_
   STATUS`** — it was never a real acquired-bundle producer (confirmed
   twice now, this pass by direct grep of `price_path_acquisition.py`: no
   assignment of `STATUS_INVALID_SOURCE_DATA` exists anywhere), so mapping
   it as an active classification target implied a live producer that
   doesn't exist — itself a form of fabrication. A persisted row somehow
   containing that legacy value now correctly falls through to the same
   `UNSUPPORTED_EVIDENCE_COMPLETENESS` fail-closed branch as any other
   unrecognized value. `classify_acquired_evidence` can no longer return
   `SOURCE_INVALID` for any input (proven by
   `test_never_returns_source_invalid_for_any_input`, 8 parameterized
   values). `SOURCE_INVALID` remains fully real via its one genuine
   trigger: `ProviderFailurePolicy` for a caught
   `PROVIDER_UNEXPECTED_COLUMN_SHAPE` exception.

3. **Explicit integrity branch now tested** — both a fake-conn regression
   test (`TestContradictoryReplayStateFailsClosed`) and a real-PostgreSQL
   HTTP-endpoint test
   (`TestContradictoryReplayStateFailsClosedThroughRealEndpoint`) force
   `classify_replay_or_acquisition` to claim `COMPATIBLE_REPLAY` while no
   compatible evidence genuinely exists, proving: zero provider calls,
   zero evidence rows, zero reports, outbox settles `FAILED_RETRYABLE`
   with `last_error_code = INTERNAL_INTEGRITY_VIOLATION`. Test-only forced
   contradiction injection via monkeypatch — production logic was not
   altered to make this naturally reachable.

4. **Stale-claimant rollback test now uses a genuine second connection**
   — the prior pass's test queried only through `pg_conn` while its own
   docstring claimed independent second-connection visibility, a real
   inconsistency between claim and implementation. Fixed:
   `psycopg.connect(pg_database_url, autocommit=True)` opens a truly
   independent connection after the forced `StaleLeaseError`, and asserts
   report count, supersession count, and outbox `claimed_by`/`status`
   from it.

5. **Calculator-gating coverage completed** for `UNSUPPORTED_EVIDENCE_
   COMPLETENESS` and `SOURCE_INVALID` via forced test-only quality
   decisions (`test_unsupported_completeness_never_invokes_the_calculator`,
   `test_source_invalid_never_invokes_the_calculator`) — both spy directly
   on `compute_excursion`/`detect_touches`/`classify_touch_order` and
   assert zero calls, rather than inferring this from control-flow review.

6. **Fresh-acquisition test's loose `status in ("COMPLETE",
   "LIMITED_EVIDENCE")` replaced with the exact expected value**
   (`LIMITED_EVIDENCE` — deterministic given `_open_and_close`'s manual
   Buy has no entry snapshot). **Report-replay test strengthened** with
   explicit provider-fail patching, structured-content byte-for-byte
   comparison, and an explicit check that no new acquisition-decision
   block was fabricated for the replayed call.

### Real-PG collection: 152 (up from 151)

New this pass: `TestContradictoryReplayStateFailsClosedThroughRealEndpoint`.

### Updated J1B traceability (exact test names)

| Requirement | Test | Type | PG15/17 |
|---|---|---|---|
| ProviderFailurePolicy has exactly 3 fields | `test_policy_has_exactly_three_load_bearing_fields` | unit | n/a |
| INVALID_SOURCE_DATA never returned by classify_acquired_evidence | `test_never_returns_source_invalid_for_any_input` (8 params) | unit | n/a |
| Legacy INVALID_SOURCE_DATA value fails closed | `test_legacy_invalid_source_data_value_falls_through_to_unsupported` | unit | n/a |
| Integrity violation fails closed (forced) | `TestContradictoryReplayStateFailsClosed` | regression | n/a |
| Integrity violation fails closed (forced, real endpoint) | `TestContradictoryReplayStateFailsClosedThroughRealEndpoint` | real-PG | pass/pass |
| UNSUPPORTED completeness never calls calculator (forced) | `test_unsupported_completeness_never_invokes_the_calculator` | regression | n/a |
| SOURCE_INVALID never calls calculator (forced) | `test_source_invalid_never_invokes_the_calculator` | regression | n/a |
| Stale-claimant rollback, genuine second connection | `test_stale_claimant_report_insert_rolls_back_atomically` | real-PG | pass/pass |
| Fresh acquisition exact outbox status | `TestFreshAcquisitionProvenanceThroughRealEndpoint` | real-PG | pass/pass |
| Report replay: no fabricated provenance, provider-fail patched | `test_existing_report_never_fabricates_new_provenance` | real-PG | pass/pass |

### Still open

Source-manifest field persistence, explicit window-contamination
traceability, `AMBIGUOUS_RESOLUTION`'s same-day/boundary trigger (only the
split trigger is wired), full 40-scenario table — unchanged from prior
passes, out of scope for J1B.
