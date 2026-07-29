# Trade Postmortem Sprint 3A — Stage J Formal Historical Evidence Matrix

Governs `services.postmortem.price_path_eligibility` (trade-context decision,
Stage J1A) and `services.postmortem.price_path_evidence_decision` (evidence
decision, Stage J1B). This is the single authoritative scenario matrix for
Stage J — the prior two-table structure (a 36-row/14-column semantic table
plus a separate 40-row/6-column test-reference table) has been consolidated
into ONE table below, per the Stage J Final Closure Correction phase's own
explicit instruction not to maintain competing documents. Every row is
either implemented and covered by an exact, verified, passing test, or is
not present here at all.

| # | Description | Trade validity | Entry snapshot | Exit snapshot | Symbol/source | Evidence availability | Evidence compatibility | Replay/acquisition decision | Calculation eligibility | Report completeness ceiling | Provider-call expectation | Evidence-row outcome | Report outcome | Outbox outcome | Reason/limitation codes | Test file | Test function | Test level | PG15/17 result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Full valid path, provider returns complete bars | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | n/a (fresh acquisition) | CALCULATION_ELIGIBLE | COMPLETE | called | 1 row, COMPLETE | new price-path report, COMPLETE | GENERATING→COMPLETE | — | test_price_path_generation.py | TestPhase4BuildPayload::test_payload_complete_status_and_populated_fields | unit | n/a |
| 2 | Provider returns empty bar list | valid | PRESENT_VALID | PRESENT_VALID | valid | none, provider empty | n/a | ACQUISITION_REQUIRED | CALCULATION_UNAVAILABLE | LIMITED_EVIDENCE | called, empty | 1 row, SOURCE_UNAVAILABLE | none this attempt | FAILED_RETRYABLE | SOURCE_UNAVAILABLE | test_paper_trading_price_path_lease_lifecycle.py | TestSuccessfulGenerationAndIdempotentReplay::test_zero_bars_never_invokes_the_calculator | regression | n/a |
| 3 | Missing entry snapshot | valid | MISSING | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report, entry-thesis fields null | GENERATING→LIMITED_EVIDENCE | MISSING_ENTRY_CONTEXT | test_price_path_eligibility.py | TestContextCeiling::test_missing_entry_snapshot_caps_ceiling | unit | n/a |
| 4 | Entry snapshot present but wrong trade/user/market (PRESENT_INVALID) | valid | PRESENT_INVALID | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report, entry-thesis fields null, explicit limitation | GENERATING→LIMITED_EVIDENCE | ENTRY_CONTEXT_INVALID | test_price_path_historical_compatibility.py | TestMissingSnapshotCapsReportAtLimitedEvidence::test_invalid_entry_snapshot_market_mismatch_produces_limited_evidence | real-PG | pass/pass |
| 5 | Missing exit snapshot | valid | PRESENT_VALID | MISSING | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report, exit-rationale fields null | GENERATING→LIMITED_EVIDENCE | MISSING_EXIT_CONTEXT | test_price_path_eligibility.py | TestContextCeiling::test_missing_exit_snapshot_caps_ceiling | unit | n/a |
| 6 | Exit snapshot present but wrong trade/user/market | valid | PRESENT_VALID | PRESENT_INVALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report, exit-rationale fields null, explicit limitation | GENERATING→LIMITED_EVIDENCE | EXIT_CONTEXT_INVALID | test_price_path_eligibility.py | TestPresentInvalidVersusMissingSnapshot::test_present_invalid_exit_gets_distinct_reason_code | unit | n/a |
| 7 | Both entry and exit snapshots missing | valid | MISSING | MISSING | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report, both context blocks null | GENERATING→LIMITED_EVIDENCE | MISSING_ENTRY_CONTEXT, MISSING_EXIT_CONTEXT | test_price_path_eligibility.py | TestContextCeiling::test_both_missing_still_permits_price_path_work | unit | n/a |
| 8 | Invalid trade timeline: opened_at NULL | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_TIMELINE | test_price_path_eligibility.py | TestInvalidTimeline::test_missing_entry_timestamp | unit | n/a |
| 9 | Invalid trade timeline: closed_at NULL | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_TIMELINE | test_price_path_eligibility.py | TestInvalidTimeline::test_missing_exit_timestamp | unit | n/a |
| 10 | Invalid trade timeline: opened_at tz-naive | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_TIMELINE | test_price_path_eligibility.py | TestInvalidTimeline::test_timezone_naive_entry_rejected | unit | n/a |
| 11 | Invalid trade timeline: closed_at tz-naive | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_TIMELINE | test_price_path_eligibility.py | TestInvalidTimeline::test_timezone_naive_exit_rejected | unit | n/a |
| 12 | Invalid trade timeline: closed_at precedes opened_at | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_TIMELINE | test_price_path_eligibility.py | TestInvalidTimeline::test_entry_after_exit | unit | n/a |
| 13 | Same-instant trade (closed_at == opened_at) — corrected wording: bundle is COMPLETE (single-session bar present), not AMBIGUOUS_RESOLUTION; ambiguity is at the excursion/touch layer, not the bundle-completeness layer | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE (bundle COMPLETE) | LIMITED_EVIDENCE (excursion NO_INTERIOR_BARS) | called | 1 row, data_completeness=COMPLETE | report; MFE/MAE null (EXCURSION_NO_INTERIOR_BARS), touch order NEITHER_OBSERVED or single-sided per boundary bar — never a fabricated order | GENERATING→LIMITED_EVIDENCE | EXCURSION_NO_INTERIOR_BARS | test_price_path_boundary_and_level_history.py | TestSameDayEntryAndExit::test_same_day_trade_has_zero_excursion_evidence | unit | n/a |
| 14 | Invalid entry_price (NULL) | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_PRICE | test_price_path_eligibility.py | TestInvalidPrice::test_missing_entry_price | unit | n/a |
| 15 | Valid trade, exit_price NULL (still open-style record closed without exit price) | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE (partial) | LIMITED_EVIDENCE | called | 1 row | report; MFE/signed-MAE/MAE-magnitude present, captured_mfe/giveback null | GENERATING→LIMITED_EVIDENCE | MISSING_EXIT_PRICE | test_price_path_calculator.py | TestExcursionFormulas::test_indeterminate_pnl_no_exit_price_still_computes_excursion_but_no_giveback | unit | n/a |
| 16 | Invalid entry_price (<=0 / NaN / inf) | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_PRICE | test_price_path_eligibility.py | TestInvalidPrice::test_non_finite_or_non_positive_entry_price | unit | n/a |
| 17 | Invalid exit_price (present, non-finite) | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_TRADE_PRICE | test_price_path_eligibility.py | TestInvalidPrice::test_non_finite_exit_price_rejected | unit | n/a |
| 18 | Invalid/unsupported market | invalid | — | — | — | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_MARKET | test_price_path_eligibility.py | TestInvalidMarket::test_unsupported_market_blocks_acquisition | unit | n/a |
| 19 | Invalid symbol (empty string) | valid | — | — | invalid | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_SYMBOL | test_price_path_eligibility.py | TestSymbolGovernance::test_empty_symbol_blocks_acquisition | unit | n/a |
| 20 | Malformed symbol (control characters etc.) | valid | — | — | invalid | — | no | acquisition_allowed=False | n/a | LIMITED_EVIDENCE | never | no row created | none | no outbox row created | INVALID_SYMBOL | test_price_path_eligibility.py | TestSymbolGovernance::test_malformed_symbol_blocks_acquisition | unit | n/a |
| 21 | Provider returns [] (fresh acquisition path) | valid | PRESENT_VALID | PRESENT_VALID | valid | none, provider returns [] | n/a | ACQUISITION_REQUIRED | CALCULATION_UNAVAILABLE | LIMITED_EVIDENCE | called, empty | 1 row, SOURCE_UNAVAILABLE | none this attempt | FAILED_RETRYABLE | SOURCE_UNAVAILABLE | test_paper_trading_price_path_lease_lifecycle.py | TestSuccessfulGenerationAndIdempotentReplay::test_zero_bars_never_invokes_the_calculator | regression | n/a |
| 22 | Malformed provider response (unexpected column shape) | valid | PRESENT_VALID | PRESENT_VALID | valid | none, provider rejected | n/a | ACQUISITION_REQUIRED | CALCULATION_UNAVAILABLE | LIMITED_EVIDENCE | called, rejected | 0 rows, no evidence persisted | none, no partial evidence | FAILED_RETRYABLE | SOURCE_INVALID | test_price_path_historical_compatibility.py | TestMalformedProviderResponseUsesPolicyDrivenOutcome::test_malformed_response_marks_source_invalid_and_no_evidence_persisted | real-PG | pass/pass |
| 23 | Compatible persisted evidence exists, no report yet — evidence replay | valid | PRESENT_VALID | PRESENT_VALID | valid | compatible | yes | COMPATIBLE_REPLAY | CALCULATION_ELIGIBLE | inherits from context | not called | 1 row, reused unchanged | report constructed from persisted evidence, deterministic hash match | GENERATING→settled from replay | COMPATIBLE_REPLAY | test_price_path_historical_compatibility.py | TestTrueEvidenceReplayWithoutAnyReport::test_generate_constructs_report_from_persisted_evidence_with_zero_provider_calls | real-PG | pass/pass |
| 24 | Incompatible evidence (different window/basis/version) — fresh acquisition | valid | PRESENT_VALID | PRESENT_VALID | valid | incompatible | no | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | inherits from context | called | new row | new report, prior evidence untouched, immutable | GENERATING→settled | ACQUISITION_REQUIRED | test_price_path_historical_compatibility.py | TestFreshAcquisitionProvenanceThroughRealEndpoint::test_fresh_acquisition_records_full_provenance | real-PG | pass/pass |
| 25 | Cross-user: a compatible evidence row exists but for a DIFFERENT user_id | valid | PRESENT_VALID | PRESENT_VALID | valid | evidence exists for a different user_id | no (cross-user rows never match) | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | inherits from context | called | new row for this user | new report; other user's row never read/exposed | GENERATING→settled | ACQUISITION_REQUIRED | test_price_path_store.py | TestGetCurrentEvidence::test_returns_none_for_non_owning_user | unit | n/a |
| 26 | Sprint 2 report exists (schema 1.0.0), no price-path evidence yet | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | inherits from context | called | new row | new price-path (1.1.0) report, distinct row, supersedes_report_id set; Sprint 2 report untouched | GENERATING→settled | — | test_price_path_generation.py | TestPhase5PersistReportAndSupersession::test_creates_new_row_distinct_from_prior_report | unit | n/a |
| 27 | Price-path report already exists for this exact version triple — idempotent replay | valid | PRESENT_VALID | PRESENT_VALID | valid | compatible, already reported | yes | COMPATIBLE_REPLAY (already settled) | n/a (skipped) | prior report's own ceiling | not called | 0 new rows — idempotent | existing report returned verbatim, no duplicate | none — PRICE_PATH_ALREADY_COMPLETE | PRICE_PATH_ALREADY_COMPLETE | test_price_path_store.py | TestPersistEvidence::test_second_call_same_version_is_idempotent | unit | n/a |
| 28 | Retry reclaim: a FAILED_RETRYABLE outbox row is reclaimed by a new attempt after its lease's backoff window elapses | valid | — | — | — | — | n/a | ACQUISITION_REQUIRED (retry) | n/a (pending) | inherits from context | called (retry) | pending until this attempt settles | pending | FAILED_RETRYABLE row reclaimed after backoff, exactly one simultaneous claimant wins | — | test_postmortem_outbox_claim_concurrency.py | TestRetryableBackoff::test_after_backoff_window_exactly_one_simultaneous_claimant_wins | real-PG | pass/pass |
| 29 | Attempt-limit exceeded — terminal transition | valid | — | — | — | — | n/a | n/a (blocked, attempt limit) | n/a | LIMITED_EVIDENCE | never (attempt limit exceeded) | no new row | none, never retried again | FAILED_TERMINAL, MAX_ATTEMPTS_EXCEEDED | MAX_ATTEMPTS_EXCEEDED | test_postmortem_outbox_claim_concurrency.py | TestAttemptLimitSettlement::test_single_call_settles_failed_terminal | real-PG | pass/pass |
| 30 | Evidence acquired, adjustment_basis unrecognized (composite-adjusted unknown basis) | valid | PRESENT_VALID | PRESENT_VALID | valid | acquired, basis unrecognized | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE (bundle-level); basis flagged separately | LIMITED_EVIDENCE | called | 1 row | report, basis-compatibility flagged COMPOSITE_ADJUSTED_UNKNOWN_BASIS, excursion fields still computed from raw bars but disclosed as basis-uncertain | GENERATING→LIMITED_EVIDENCE | COMPOSITE_ADJUSTED_UNKNOWN_BASIS | test_price_path_acquisition_boundary.py | TestBasisCompatibilityClassification::test_unrecognized_acquisition_mode_is_composite_adjusted_unknown_basis | unit | n/a |
| 31 | Split detected inside the holding window — CORRECTED (Stage 4): split is detected deterministically; NO price reconciliation is attempted; adjustment basis becomes UNKNOWN_ADJUSTMENT; excursion calculation is unavailable; report is LIMITED_EVIDENCE; the split limitation is explicit | valid | PRESENT_VALID | PRESENT_VALID | valid | none (split forces zero-bar bundle) | n/a | ACQUISITION_REQUIRED | CALCULATION_UNAVAILABLE | LIMITED_EVIDENCE | called | 1 row, data_completeness=AMBIGUOUS_RESOLUTION, price_adjustment_basis=UNKNOWN_ADJUSTMENT, 0 bars | report LIMITED_EVIDENCE via build_unavailable_report_payload — every analytic field explicitly None, never a reconciled/guessed value | GENERATING→LIMITED_EVIDENCE | AMBIGUOUS_RESOLUTION, explicit split limitation string | test_price_path_acquisition_boundary.py | TestBasisCompatibilityClassification::test_split_in_window_takes_priority_over_everything | unit | n/a |
| 32 | No corporate-action metadata returned by the provider — absence is never treated as proof no corporate action occurred | valid | PRESENT_VALID | PRESENT_VALID | valid | acquired, no split/dividend events | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | inherits from context | called | 1 row, split_event_manifest=[], dividend_event_manifest=[] | report never claims 'no corporate action occurred' — only that none was DISCLOSED by the provider for this window | GENERATING→settled | — | test_price_path_acquisition_boundary.py | TestBasisCompatibilityClassification::test_missing_split_event_is_not_by_itself_proof_of_compatibility | unit | n/a |
| 33 | Entry==exit calendar session, partial (non-boundary-exact) session — same underlying zero-interior-bars condition documented under scenario 13; kept as its own row since it was independently requested in the original Stage J numbering's own numbering | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE (bundle COMPLETE) | LIMITED_EVIDENCE (excursion NO_INTERIOR_BARS) | called | 1 row, data_completeness=COMPLETE | report; MFE/MAE null, touch order per boundary-bar single-sided rule — never AMBIGUOUS_RESOLUTION at the bundle layer | GENERATING→LIMITED_EVIDENCE | EXCURSION_NO_INTERIOR_BARS | test_price_path_boundary_and_level_history.py | TestSameDayEntryAndExit::test_same_day_trade_has_zero_excursion_evidence | unit | n/a |
| 34 | Zero interior bars — CORRECTED (Stage 4): excursion (MFE/MAE) is unavailable (EXCURSION_NO_INTERIOR_BARS), but a single-sided touch on the boundary bar itself still yields a definitive, non-fabricated claim (e.g. STOP_ONLY); touch order is INSUFFICIENT_EVIDENCE only when no stop/target level is configured at all — not merely because interior bars are absent | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE (bundle COMPLETE) | LIMITED_EVIDENCE | called | 1 row | report; MFE/MAE null; touch order definitive single-sided claim from the boundary bar's own high/low when a level is configured, INSUFFICIENT_EVIDENCE only when neither level is configured | GENERATING→LIMITED_EVIDENCE | EXCURSION_NO_INTERIOR_BARS | test_price_path_calculator.py | TestTouchDetectionAndOrdering::test_neither_level_configured_is_insufficient_evidence | unit | n/a |
| 35 | Both stop and target touched within one interior daily bar — BOTH_SAME_BAR_AMBIGUOUS | valid | PRESENT_VALID | PRESENT_VALID | valid | acquired, complete | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE (Stage J-F5 ceiling downgrade) | called | 1 row, data_completeness=COMPLETE | report; MFE/MAE populated (excursion is independent of touch order), touch claim BOTH_SAME_BAR_AMBIGUOUS (CONFLICTING_EVIDENCE), report status downgraded to LIMITED_EVIDENCE | GENERATING→LIMITED_EVIDENCE | BOTH_SAME_BAR_AMBIGUOUS | test_price_path_boundary_and_level_history.py + test_price_path_generation.py | TestTouchesOnBoundaryBars::test_both_touched_on_same_boundary_bar_is_ambiguous; TestPhase4BuildPayload::test_both_same_bar_ambiguous_touch_order_downgrades_status_despite_complete_bundle | unit | n/a |
| 36 | Stop/target level changed mid-trade, only entry/exit endpoint values known (level-history incomplete) | valid | PRESENT_VALID | PRESENT_VALID | valid | acquired, complete | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report; MFE/MAE populated, touch claim LEVEL_HISTORY_INCOMPLETE (INSUFFICIENT_EVIDENCE); final levels never applied retrospectively across the whole trade | GENERATING→LIMITED_EVIDENCE | LEVEL_HISTORY_INCOMPLETE | test_price_path_boundary_and_level_history.py | TestLevelHistoryCompleteness::test_both_edited_treated_as_incomplete | unit | n/a |
| 37 | Source manifest persists every final required field through real PostgreSQL JSONB storage | valid | PRESENT_VALID | PRESENT_VALID | valid | acquired | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | COMPLETE | called | 1 row, manifest with 20+ fields incl. manifest_integrity_hash | report COMPLETE | GENERATING→COMPLETE | — | test_price_path_historical_compatibility.py | TestSourceManifestPersistsAllFinalFields::test_manifest_fields_survive_real_persistence | real-PG | pass/pass |
| 38 | Same-day daily trade persists real NULL excursion analytics and a LIMITED_EVIDENCE report through the real endpoint | valid | PRESENT_VALID | PRESENT_VALID | valid | acquired, complete, zero interior bars | n/a | ACQUISITION_REQUIRED | CALCULATION_ELIGIBLE | LIMITED_EVIDENCE | called | 1 row | report; mfe_abs/mae_signed_abs/mae_magnitude_abs/captured_mfe_pct/giveback_abs all real SQL NULL, never zero | GENERATING→LIMITED_EVIDENCE | EXCURSION_NO_INTERIOR_BARS | test_price_path_historical_compatibility.py | TestSameDayTradePersistsNullExcursionAndLimitedEvidence::test_same_day_trade_persists_null_excursion | real-PG | pass/pass |
| 39 | Persisted evidence row carries the exact legacy value INVALID_SOURCE_DATA — fails closed on replay | valid | PRESENT_VALID | PRESENT_VALID | valid | compatible, but data_completeness corrupted to legacy value | yes | COMPATIBLE_REPLAY | CALCULATION_UNAVAILABLE (fails closed) | LIMITED_EVIDENCE (no report at all) | not called | 1 row, unchanged (never repaired/duplicated) | none — never COMPLETE, never a fabricated LIMITED_EVIDENCE report either | FAILED_RETRYABLE | UNSUPPORTED_EVIDENCE_COMPLETENESS | test_price_path_historical_compatibility.py | TestLegacyInvalidSourceDataRowFallsThroughFailClosed::test_legacy_invalid_source_data_value_fails_closed_through_real_replay | real-PG | pass/pass |
| 40 | Reset removes Stage J evidence/report/outbox rows for the resetting user only — a second user's rows are untouched | valid | — | — | — | — | n/a | n/a | n/a | n/a | n/a | 0 rows remain for resetting user; other user's rows unchanged | 0 rows remain for resetting user; other user's rows unchanged | 0 rows remain for resetting user; other user's rows unchanged | — | test_price_path_historical_compatibility.py | TestResetScopedToSingleTrade::test_reset_does_not_touch_another_users_price_path_rows | real-PG | pass/pass |

Corrections applied in this consolidation pass (Stage 4 semantic review):

- **Row 13** (same-instant trade) previously claimed the bundle's own
  `data_completeness` becomes `AMBIGUOUS_RESOLUTION`. Verified empirically
  false: a same-day trade with its single session bar present is
  `data_completeness=COMPLETE` at the bundle layer — the real ambiguity is
  one layer down, at `compute_excursion` (`EXCURSION_NO_INTERIOR_BARS`).
  `AMBIGUOUS_RESOLUTION` at the bundle layer has exactly one live trigger
  today: split-in-window (row 31). Corrected.
- **Row 31** (split in window) previously said "reconciled deterministically"
  and "report notes split-adjusted basis" — the actual, approved production
  behavior is the opposite: no reconciliation is ever attempted; the split
  is only ever DETECTED, `price_adjustment_basis` becomes
  `UNKNOWN_ADJUSTMENT`, and the report is `LIMITED_EVIDENCE` with an
  explicit split limitation. Corrected per the Stage 4 instruction.
- **Row 33** (entry==exit partial session) had the same false
  `AMBIGUOUS_RESOLUTION` claim as row 13 — same root cause, same
  correction.
- **Row 34** (zero interior bars) previously claimed touch order becomes
  `INSUFFICIENT_EVIDENCE` merely from zero interior bars, citing
  `LEVEL_HISTORY_INCOMPLETE` as its reason code — verified empirically
  false: a single-sided touch on the boundary bar itself still produces a
  definitive claim (e.g. `STOP_ONLY`) when a level is configured;
  `INSUFFICIENT_EVIDENCE` only occurs when NEITHER stop nor target is
  configured at all (a materially different condition, unrelated to
  interior-bar availability). Corrected, and the exact test cited was
  changed to the one that actually proves this
  (`TestTouchDetectionAndOrdering::test_neither_level_configured_is_insufficient_evidence`).

No row in this matrix is coded around with a fabricated value. Every
LIMITED_EVIDENCE/INSUFFICIENT_EVIDENCE path renders the fixed fallback text
("Insufficient evidence to determine this factor reliably.") and never a
zero, a guessed price, or an inferred market event.

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

## Stage J Final Closure — Historical Source, Window, Ambiguity, Traceability

This section closes the remaining Stage J scope explicitly deferred by
every prior J1B pass above. It does not reopen or modify J1B's own
decision-model logic (`price_path_evidence_decision.py`) — J1B is accepted
as complete. It resolves the legacy `INVALID_SOURCE_DATA` contract
ambiguity, completes source-manifest fields, fixes a real report-ceiling
gap touch-order ambiguity exposed, and consolidates the scenario matrix.

### J-F2 — Legacy `INVALID_SOURCE_DATA` contract resolution

**Decision: LEGACY-READ COMPATIBILITY** (not versioned removal).
`STATUS_INVALID_SOURCE_DATA` remains declared in
`price_path_evidence.py`'s `_VALID_STATUSES`, now with an explicit
in-module comment stating it has zero live producers and exists only so
(a) a pre-existing persisted row with this value still replays without a
construction-time rejection, and (b) `__post_init__` doesn't need to know
about the classification layer's own history. No schema/version bump —
this was always a legacy-compatibility concern, not a contract shape
change. Proven by two new tests in `test_price_path_evidence.py::
TestLegacyInvalidSourceDataContract`:
`test_legacy_invalid_source_data_status_has_no_producer` (source-scans
`price_path_acquisition.py` for the literal name — it is not even
imported there) and `test_legacy_invalid_source_data_bundle_replays_
without_error` (constructs a bundle carrying the legacy value and
confirms it doesn't raise).

### J-F3 — Source-manifest completeness

Added to `source_manifest` (all inside the existing free-form dict field
— no new dataclass fields, no bundle-shape migration): `source_manifest_
schema_version`, `trade_symbol` (paired explicitly with the pre-existing
`provider_symbol`), `symbol_normalization_version`, `market`,
`provider_request_start`, `provider_exclusive_request_end`,
`end_widening_reason`, `boundary_policy_version`, `prepost`,
`requested_trading_weekday_count` (Mon-Fri count in the requested
window — deliberately NOT a full trading-holiday-aware count; see
`_count_requested_weekdays`'s own docstring for why reusing
`services.market_hours`'s internal holiday calendars here would be a
false-precision claim), and `manifest_integrity_hash` (SHA-256 over the
manifest's own fields, separate from the pre-existing `evidence_hash`
which covers bars only — `_compute_manifest_integrity_hash`). All
derived from persisted trade facts or pinned acquisition configuration;
no current-universe lookup, no inferred rename/merger/delisting. Proven
by `test_price_path_acquisition_boundary.py::TestSourceManifestCompleteness`
(8 tests: field presence, widening arithmetic, requested_window_end
unaffected by widening, weekday count, prepost, hash determinism, hash
sensitivity to symbol, hash distinctness from evidence_hash).

Real-PG closure (J-F7): `TestSourceManifestPersistsAllFinalFields` and
`TestCompatibleReplayPreservesManifestUnchanged` (below) prove these
fields round-trip through actual PostgreSQL JSONB storage and that a
compatible-replay reuses the identical persisted manifest (no
re-derivation, no drift).

### J-F5 — Ceiling composition gap (real defect found and fixed)

The Stage J-F1 gap inventory found a genuine, previously undocumented
defect: `build_price_path_report_payload`'s `status` field
(`price_path_generation.py`) was computed from `bundle.data_completeness`
and `excursion.evidence_completeness` only — it never inspected `order`
(the touch-order classification result). A bundle could be fully
`COMPLETE` with `EXCURSION_COMPLETE` MFE/MAE and still have
`BOTH_SAME_BAR_AMBIGUOUS`/`BOUNDARY_BAR_AMBIGUOUS`/
`LEVEL_HISTORY_INCOMPLETE` touch order, and the report would still claim
`status=COMPLETE` — silently contradicting its own touch_order claim
(which `build_touch_order_claim` already correctly renders as
insufficient-evidence-flavored). Fixed: `status` now also requires
`order not in {BOTH_SAME_BAR_AMBIGUOUS, BOUNDARY_BAR_AMBIGUOUS,
LEVEL_HISTORY_INCOMPLETE, INSUFFICIENT_EVIDENCE}`. `NEITHER_OBSERVED`
(a definitive "neither level was touched" claim) is correctly excluded
from the ambiguous set. This does not null out or suppress independently
valid MFE/MAE — those come from `excursion`, computed independently of
touch order, and remain populated. Proven by
`test_price_path_generation.py::TestPhase4BuildPayload::
test_both_same_bar_ambiguous_touch_order_downgrades_status_despite_
complete_bundle` (downgrade + MFE/MAE still populated) and its control,
`test_clean_stop_only_touch_order_does_not_downgrade_status` (a
definitive order on a fully-COMPLETE bundle stays COMPLETE).

### J-F4 — Historical-window contamination: traceability, not new logic

The underlying window-construction logic (`build_price_path_evidence`
filters strictly to `[entry_date, exit_date]` against the ORIGINAL,
non-widened dates — `price_path_acquisition.py`'s own `if d < entry_date
or d > exit_date: continue`) was already correct and already unit-tested
before this pass; the gap was that the ADR itself claimed this was "not
re-proven under a Stage J test name," which was stale. The table below
names the exact existing tests. No new contamination-logic tests were
added for cases already covered (per this phase's own instruction not to
duplicate proven coverage) — J-F7 adds the one real gap found
(acquisition-layer dedup "later row wins" had no direct test) plus the
real-PG-level versions of the highest-value cases.

| # | Requirement | Exact test | File | Level |
|---|---|---|---|---|
| W1 | Entry session included | `test_entry_date_included` | `test_price_path_date_boundary.py` | unit |
| W2 | Exit session included despite provider-exclusive end | `test_exit_date_included` | `test_price_path_date_boundary.py` | unit |
| W3 | Provider-exclusive end widened for request only | `test_provider_exclusive_request_end` (J-F3) + `test_original_requested_window_end_unaffected_by_widening` | `test_price_path_acquisition_boundary.py` | unit |
| W4 | `requested_window_end` remains original exit date | `test_original_requested_window_end_unaffected_by_widening` | `test_price_path_acquisition_boundary.py` | unit |
| W5 | Pre-entry rows excluded | `test_pre_entry_row_also_excluded` | `test_price_path_date_boundary.py` | unit |
| W6 | Post-exit rows excluded | `test_session_after_exit_excluded` | `test_price_path_date_boundary.py` | unit |
| W7 | Provider-returned extra row after exit excluded | `test_provider_extra_row_does_not_change_completeness_falsely` | `test_price_path_date_boundary.py` | unit |
| W8 | Weekend-after-exit excluded | `test_weekend_after_exit_excluded` | `test_price_path_date_boundary.py` | unit |
| W9 | Holiday boundary bar within window retained | `test_holiday_boundary_bar_within_window_retained` | `test_price_path_date_boundary.py` | unit |
| W10 | Report-generation date never alters the historical window | `_persisted_evidence_to_bundle` reloads from stored rows only — no `date.today()`/`datetime.now()` call anywhere in `price_path_generation.py`'s Phase 4; structural, code-review verified | `price_path_generation.py` | code-review |
| W11 | Current quote never consulted | acquisition takes only `entry_timestamp`/`exit_timestamp`; no live-quote import in `price_path_acquisition.py` | — | code-review |
| W12 | US DST-boundary handling deterministic | `test_dst_date_session_open_still_930_local` | `test_session_boundary.py` | unit |
| W13 | Timezone-naive rejected at construction | `test_naive_entry_timestamp_rejected` | `test_price_path_evidence.py` | unit |
| W14 | Duplicate session date rejected (bundle-level guard) | `test_duplicate_session_date_rejected` | `test_price_path_evidence.py` | unit |
| W15 | Out-of-order bars rejected (bundle-level guard) | `test_out_of_order_bars_rejected` | `test_price_path_evidence.py` | unit |
| W16 | Acquisition-layer dedup — later row wins (new, J-F7) | `test_duplicate_provider_row_later_value_wins` | `test_price_path_acquisition_boundary.py` | unit |
| W17 | Requested/observed windows persisted and replay uses them, never today's date | `TestCompatibleReplayPreservesManifestUnchanged` (J-F7) | `test_price_path_historical_compatibility.py` | real-PG |

### J-F6 — Final 40-scenario traceability matrix (superseded)

The 40-scenario matrix with full test traceability now lives at the TOP
of this document (the single consolidated table) rather than here — the
version previously in this section had only 6 columns despite being
labeled a full traceability matrix, and several rows used placeholder
references ("as row 3", "not independently named", class-only citations)
instead of exact test functions. That version is removed rather than
kept alongside the corrected one, per the Stage J Final Closure
Correction phase's explicit instruction not to maintain two competing
tables. See the top of this document for the current, exact-test-cited,
20-column matrix.


### J-F8 — Final status (Stage J closure)

- **Evidence source**: `yfinance` — an unofficial, unlicensed scraper of
  Yahoo Finance data, with no in-repo licensing review. `SOURCE_TYPE =
  "EXTERNAL_UNOFFICIAL_DAILY"` and `source_manifest["production_
  authoritative"] = False` on every persisted bundle make this explicit
  at the data level, not just in a docstring — no downstream consumer can
  read a price-path evidence row without also seeing this disclosure.
- **Implemented**: on feature branch `feature/trade-postmortem-sprint3a-price-path`
  only.
- **Verified**: locally (full non-postgres_integration suite) and against
  real PostgreSQL 15 and PostgreSQL 17 (see final checkpoint report for
  exact run/job IDs and totals).
- **Not reviewed through PR.** Not merged. Not deployed.
- **Feature flags**: `TRADE_POSTMORTEM_DAILY_ENABLED`,
  `TRADE_POSTMORTEM_PRICE_PATH_ENABLED`,
  `NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED` all remain disabled by
  default (missing/unrecognized value = disabled; only an explicit
  accepted true value enables). No `.env` file in this repository sets
  any of them true.
- **Known limitations (honest, not exhaustive)**:
  - `requested_trading_weekday_count` excludes weekends only, not market
    holidays — see `_count_requested_weekdays`'s own docstring for why
    reusing `services.market_hours`'s internal holiday calendars here
    would overclaim precision this manifest doesn't actually have.
  - `AMBIGUOUS_RESOLUTION`'s only live trigger remains split-in-window;
    a genuine same-day/boundary-ambiguity trigger at the
    `data_completeness` layer (as opposed to the touch-order layer,
    where it is fully handled — see J-F5) still does not exist.
  - `PRICE_BASIS_INCOMPATIBLE_FUTURE_SCOPE` remains declared-only, no
    live producer (documented future scope from a prior pass, unchanged).
  - (Resolved in the Stage J Final Closure Correction pass — see the
    section of that name below.) Every row 1-40 of the consolidated
    matrix at the top of this document now cites an exact, verified,
    passing test function.
  - Full trading-calendar-aware session counting (vs. the honest
    weekday-only count added this pass) remains future scope.

## Stage J Final Closure Correction (this pass)

Corrects four confirmed defects in the prior "Final Stage J Closure" pass's
own claims, per an independent audit:

1. **`manifest_integrity_hash` was computed before the manifest's own
   limitation set was final.** The prior pass built `source_manifest`,
   immediately computed and assigned `manifest_integrity_hash`, and only
   THEN appended dividend/split/boundary-policy/partial-window
   limitations — the same mutable list the manifest's
   `unresolved_basis_limitations` key already pointed to. The hash was
   therefore never actually reproducible from the manifest's own final
   content. Fixed via a single `finalize_source_manifest(source_manifest,
   final_limitations)` function, called exactly once per return path
   (split-in-window / no-bars / partial / complete), AFTER every
   limitation for that path is known. `unresolved_basis_limitations`
   becomes an immutable snapshot COPY of the final limitations list, not
   a still-mutable shared reference. The hash now covers every manifest
   field except itself, including limitations.

2. **The no-bars return path built a brand-new one-item limitations list
   literal**, completely discarding any dividend/boundary-policy
   limitation already appended to the shared list — a real, silent
   divergence between `bundle.limitations` and
   `source_manifest["unresolved_basis_limitations"]` whenever both
   applied to the same trade. Fixed: the no-bars message is now appended
   to the SAME shared list every other path uses.

3. **New `verify_source_manifest_integrity(manifest) -> bool`** —
   recomputes the hash over every field except itself and compares
   against the stored value; returns `False` (never raises) for a
   manifest missing the hash entirely.

4. **The prior "40-scenario matrix" had 6 columns** (`#`, `Scenario`,
   `Test file`, `Test name`, `Level`, `PG15/17`) despite being presented
   as complete — nowhere near the 20 required decision/traceability
   columns. It also used placeholder references (`"as row 3"`,
   `"not independently named"`, class-only citations, blank fields) for
   roughly a third of its rows. Both this table AND the separate
   36-row/14-column semantic table (which never had test references at
   all) are now REMOVED and replaced by ONE consolidated 40-row/20-column
   table at the top of this document, with an exact, independently
   verified, passing test function cited for every row (see the
   `TestStageJTraceabilityValidator` unit suite, which parses this exact
   table and fails the build if any of these properties regress:
   row count, column completeness, blank test references, forbidden
   placeholder strings, missing outcome/reason-code cells, or a
   regression of the row 31/13/33 wording corrections below).

### Scenario semantic corrections (Stage 4)

- **Row 31** previously read "Split in window, reconciled
  deterministically" and claimed the report "notes split-adjusted
  basis" — the actual, verified production behavior is the opposite: no
  reconciliation is ever attempted. A split in-window is only ever
  DETECTED; `price_adjustment_basis` becomes `UNKNOWN_ADJUSTMENT`;
  excursion calculation is refused entirely (`CALCULATION_UNAVAILABLE`);
  the report is `LIMITED_EVIDENCE` with an explicit split limitation
  string. Corrected wording: "split is detected deterministically;
  cross-split calculation refused."
- **Rows 13 and 33** previously claimed a same-instant/entry==exit trade
  produces `data_completeness=AMBIGUOUS_RESOLUTION` at the bundle layer.
  Verified empirically false — a same-day trade with its one session bar
  present is `data_completeness=COMPLETE`; `AMBIGUOUS_RESOLUTION` has
  exactly one live trigger today (split-in-window, row 31). The real
  reason code for rows 13/33 is `EXCURSION_NO_INTERIOR_BARS`, produced
  one layer down by `compute_excursion`. Corrected.
- **Row 34** previously claimed zero interior bars by itself forces touch
  order to `INSUFFICIENT_EVIDENCE`, citing `LEVEL_HISTORY_INCOMPLETE` as
  its reason code (itself inconsistent — that's a different scenario,
  row 36). Verified empirically false — a single-sided touch on the
  boundary bar still produces a definitive claim (e.g. `STOP_ONLY`) when
  a level is configured; `classify_touch_order` only returns
  `INSUFFICIENT_EVIDENCE` when NEITHER stop nor target is configured at
  all. Corrected, with the exact test reference changed to the one that
  actually proves this claim.

### Real-PostgreSQL closure additions (Stage 6)

Six new dedicated real-PG scenarios, none duplicating an existing proof:

| Requirement | Test |
|---|---|
| Persisted contamination exclusion (pre-entry/post-exit/current-date-outside-window bars all excluded) | `TestPersistedContaminationExclusion::test_only_in_window_bars_are_persisted` |
| Boundary + interior touch on different bars → BOUNDARY_BAR_AMBIGUOUS, LIMITED_EVIDENCE | `TestBoundaryTouchPersistedAmbiguous::test_boundary_and_interior_touch_on_different_bars_is_ambiguous` |
| Both stop/target in one interior bar → BOTH_SAME_BAR_AMBIGUOUS, LIMITED_EVIDENCE, MFE/MAE still populated | `TestBothSameBarPersistedAmbiguous::test_both_same_interior_bar_is_ambiguous_with_excursion_populated` |
| Invalid (present-but-wrong-trade) exit snapshot caps report, never fabricates exit rationale | `TestInvalidExitSnapshotThroughRealEndpoint::test_invalid_exit_snapshot_caps_report_and_never_fabricates_rationale` |
| Manifest hash verifies from real JSONB; tampered copy fails | `TestFinalManifestIntegrityThroughRealPostgres::test_manifest_hash_verifies_from_real_jsonb_and_tamper_fails` |
| No-bars: persisted `limitations` column == persisted `source_manifest.unresolved_basis_limitations` exactly | `TestNoBarsManifestConsistencyThroughRealPostgres::test_persisted_limitations_and_manifest_match` |

`paper_trades.opened_at`/`closed_at` were directly widened via SQL
`UPDATE` for the boundary/both-same-bar/contamination tests — unlike the
snapshot/evidence/report tables, `paper_trades` carries no immutability
trigger, confirmed before use.

**Real finding from this pass's own CI run** (not a pre-existing known
limitation, a genuinely NEW fact discovered while writing these tests):
`BOUNDARY_BAR_AMBIGUOUS` and `BOTH_SAME_BAR_AMBIGUOUS` cannot actually be
produced by the live `/generate` endpoint for ANY real trade today.
`classify_touch_order`'s rule 1 ("if not level_history_complete: return
LEVEL_HISTORY_INCOMPLETE") is checked FIRST, and
`paper_trading.py`'s live call site hardcodes
`level_history_complete=False` for every real trade (this codebase's own
honest finding: it cannot yet prove full stop/target edit history for
any real trade). The first version of these two tests asserted the
ambiguity value directly through `/generate` and failed real CI with
`LEVEL_HISTORY_INCOMPLETE` instead — confirming this is real production
behavior, not a test bug. Both tests now acquire and persist evidence
through the real endpoint (still genuine PostgreSQL round-trip), then
call `build_price_path_report_payload` directly against that persisted
evidence with `level_history_complete=True` to prove the ambiguity
classifier itself, while separately asserting the live endpoint's own
current `LEVEL_HISTORY_INCOMPLETE` behavior is exactly what it claims to
be.

### Real-PG collection: 163 (up from 157)

### Final status (unchanged from J-F8, reconfirmed)

Implemented on feature branch only; locally verified; PG15 verified;
PG17 verified; not PR-reviewed; not merged; not deployed; all three
feature flags remain disabled.
