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

## Current Architecture (authoritative — supersedes all narrative below)

- **`ProviderFailurePolicy`** (`price_path_evidence_decision.py`) is the
  SOLE provider-failure authority, exactly 3 fields:
  `evidence_status`, `immediate_outbox_outcome`, `sanitized_error_code`.
  `classify_provider_failure` (an earlier, duplicate classifier) was
  removed entirely — do not cite it as live code.
- **Active bundle `data_completeness` states**: `COMPLETE`, `PARTIAL`,
  `UNAVAILABLE`, `AMBIGUOUS_RESOLUTION` — 4 values, all with real live
  producers in `price_path_acquisition.build_price_path_evidence`.
- **`STATUS_INVALID_SOURCE_DATA` is legacy-read-only**: declared in
  `price_path_evidence.py` for backward-compatible replay of any
  pre-existing persisted row only; zero live producers; any row
  carrying it (fresh or legacy) is classified fail-closed as
  `UNSUPPORTED_EVIDENCE_COMPLETENESS` by
  `price_path_evidence_decision.classify_acquired_evidence`.
- **Acquisition provenance** (`HistoricalEvidenceAcquisitionDecision` —
  WHERE evidence came from) and **evidence quality**
  (`HistoricalEvidenceQualityDecision` — WHETHER it's usable) remain two
  separate, never-merged decisions, persisted as two separate
  `structured_report["price_path"]` blocks.
- **Manifest-integrity validation is load-bearing and now covers the
  FULL semantic contract** (Stage J Final Semantic Reconciliation Stage 2,
  expanded to 30 required fields plus 13 cross-checks in Stage J3
  Stage 5): before ANY compatible persisted evidence row is used for
  replay — and, as of Stage J3, before a FRESHLY BUILT bundle is even
  persisted — `price_path_acquisition.validate_manifest_compatibility`
  is checked: every Stage J-F3 manifest field present; the integrity
  hash verifies (an ordinary SHA-256 self-consistency check, NOT
  cryptographic authentication — it detects corruption/drift, not an
  attacker who rewrites both content and hash together); schema/
  normalization/boundary-policy versions supported; pinned acquisition
  arguments match; identity fields (source_id/type/version, symbols,
  market, interval) match the row's own other columns; provider-
  exclusive-end equals requested_window_end + 1 day; split/dividend
  dates are valid ISO dates inside the window; manifest limitations
  equal the row's own persisted `limitations` column exactly;
  production_authoritative is exactly False; source_scope/source_type
  match their fixed constants. Any failure fails closed
  (`MANIFEST_INTEGRITY_VIOLATION`, zero provider calls, zero calculator
  calls, zero new evidence under the same identity, zero report,
  original row(s) unchanged). A legacy row persisted before this
  governance existed correctly fails this check — never silently
  treated as compatible merely because an old version identity happens
  to match.
- **One authoritative version identity** (Stage J3, Stage 2):
  `services.postmortem.price_path_identity.CURRENT_PRICE_PATH_SOURCE_
  IDENTITY` is the sole source every price-path module reads version
  constants from — `EVIDENCE_BUNDLE_SCHEMA_VERSION`, `SOURCE_VERSION`,
  `SOURCE_ID_YFINANCE_DAILY`, `SOURCE_MANIFEST_SCHEMA_VERSION`,
  `SYMBOL_NORMALIZATION_VERSION`, `BOUNDARY_POLICY_VERSION`,
  `PRICE_PATH_REPORT_SCHEMA_VERSION`, `CALCULATION_RULES_VERSION`.
  `load_generation_context`'s compatible-evidence lookup and
  `_price_path_target_identity`'s report/outbox identity now read from
  the exact same object — the two can no longer silently drift apart.
  `SOURCE_VERSION` bumped `1.0.0` → `1.1.0` this pass (Stage J3 Stage 3)
  because the manifest/compatibility semantics genuinely changed
  materially since 1.0.0 was pinned. Old 1.0.0 rows remain immutable,
  readable, and never block a fresh 1.1.0 acquisition (the lookup simply
  no longer finds them — proven in `TestLegacyAndCurrentEvidenceCoexistence`).
- **India and US real-PostgreSQL assurance is symmetric** (Stage J3,
  Stage 6): both markets have dedicated fresh-acquisition, replay, and
  cross-market-isolation real-PG tests — RELIANCE/`.NS`/Asia-Kolkata for
  India, AAPL/no-suffix/America-New_York (+ one explicit DST-date case)
  for US.
- **Trade-context decision provenance** (Stage 7): J1A's own
  `eligibility.reason_codes`/`limitations` (e.g. `EXIT_CONTEXT_INVALID`)
  are persisted as their own `structured_report["price_path"]
  ["trade_context_decision"]` block and folded into `evidence_gaps`, via
  `persist_price_path_report`'s `trade_context_decision` parameter.
- **Touch pattern vs. governed touch-order conclusion**: **NOT YET
  separated** as of this pass. `classify_touch_order` still returns one
  collapsed enum, and `level_history_complete=False` (hardcoded for
  every real trade today) means `LEVEL_HISTORY_INCOMPLETE` wins over
  `BOTH_SAME_BAR_AMBIGUOUS`/`BOUNDARY_BAR_AMBIGUOUS` through the live
  endpoint for any real trade, exactly as row 35's own citation already
  documents (a unit-level, not live-endpoint, proof). Separating
  "what was observed" from "what can be concluded given level-history
  limitations" into two independent, separately-persisted fields is
  scoped, deliberately deferred work — see the Stage J Final Semantic
  Reconciliation checkpoint report for the explicit reasoning.
- **Zero-bar fresh acquisition** (`SOURCE_UNAVAILABLE`): always persists
  one honest zero-bar evidence row and produces one honest
  `LIMITED_EVIDENCE` report via `build_unavailable_report_payload` —
  never `FAILED_RETRYABLE` with no report (that outcome is reserved for
  `SOURCE_INVALID`/`UNSUPPORTED_EVIDENCE_COMPLETENESS`, which have
  `fresh_persistence_permitted=False`).

| # | Description | Trade validity | Entry snapshot | Exit snapshot | Symbol/source | Evidence availability | Evidence compatibility | Replay/acquisition decision | Calculation eligibility | Report completeness ceiling | Provider-call expectation | Evidence-row outcome | Report outcome | Outbox outcome | Reason/limitation codes | Test file | Test function | Test level | PG15/17 result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Full valid path, provider returns complete bars | valid | PRESENT_VALID | PRESENT_VALID | valid | none | n/a | n/a (fresh acquisition) | CALCULATION_ELIGIBLE | COMPLETE | called | 1 row, COMPLETE | new price-path report, COMPLETE | GENERATING→COMPLETE | — | test_price_path_generation.py | TestPhase4BuildPayload::test_payload_complete_status_and_populated_fields | unit | n/a |
| 2 | Provider returns empty bar list — CORRECTED: fresh SOURCE_UNAVAILABLE evidence is still persisted and still produces an honest zero-bar LIMITED_EVIDENCE report (never FAILED_RETRYABLE — that outcome is reserved for SOURCE_INVALID/UNSUPPORTED_EVIDENCE_COMPLETENESS, which produce no report at all) | valid | PRESENT_VALID | PRESENT_VALID | valid | none, provider empty | n/a | ACQUISITION_REQUIRED | CALCULATION_UNAVAILABLE | LIMITED_EVIDENCE | called, empty | 1 row, data_completeness=UNAVAILABLE | 1 row, LIMITED_EVIDENCE, every analytic field explicit None via build_unavailable_report_payload, calculator never invoked | GENERATING→LIMITED_EVIDENCE | SOURCE_UNAVAILABLE | test_paper_trading_price_path_lease_lifecycle.py | TestSuccessfulGenerationAndIdempotentReplay::test_zero_bars_never_invokes_the_calculator | regression | n/a |
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
| 21 | Provider returns [] (fresh acquisition path) — CORRECTED, the same real code path as scenario 2 (both are the fresh-acquisition zero-bar case; kept as its own row only because the original Stage J request numbered them separately) | valid | PRESENT_VALID | PRESENT_VALID | valid | none, provider returns [] | n/a | ACQUISITION_REQUIRED | CALCULATION_UNAVAILABLE | LIMITED_EVIDENCE | called, empty | 1 row, data_completeness=UNAVAILABLE | 1 row, LIMITED_EVIDENCE, every analytic field explicit None, calculator never invoked | GENERATING→LIMITED_EVIDENCE | SOURCE_UNAVAILABLE | test_price_path_historical_compatibility.py | TestZeroBarLifecycleThroughRealEndpoint::test_zero_bars_persists_unavailable_evidence_and_limited_report | real-PG | pass/pass |
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

> **HISTORICAL SECTION NOTICE**: everything from here through the end of the "Stage J1B-Final-Reconciliation" section describes a chronological sequence of now-superseded intermediate states, each corrected by a LATER section further down this document. In particular: `classify_provider_failure` (mentioned below as the live classifier) was fully removed in the later "Stage J1B-Assurance-Closure" section -- `get_provider_failure_policy`/`ProviderFailurePolicy` is the sole, current provider-failure authority. References below to a "5-value" data_completeness enum including `INVALID_SOURCE_DATA` as an active value were corrected in the later "Stage J1B-Final-Reconciliation" section -- INVALID_SOURCE_DATA is legacy-read-only today, confirmed by `price_path_evidence.py`'s own in-module comment and `TestLegacyInvalidSourceDataContract`. Do not cite this section as current architecture; see "Current Architecture (authoritative)" near the top of this document, and the consolidated 40-row matrix above it.

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
| Invalid (present-but-wrong-market) exit snapshot caps report at LIMITED_EVIDENCE | `TestInvalidExitSnapshotThroughRealEndpoint::test_invalid_exit_snapshot_persists_explicit_structured_reason` |
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

## Stage J3 — Versioned Evidence Identity, Complete Manifest Contract, India/US Assurance (this pass)

Closes the version-identity and both-market-assurance gate exposed by
the Stage J Final Semantic Reconciliation pass's own load-bearing
manifest validator.

### Root cause found and fixed

`price_path_generation.load_generation_context`'s compatible-evidence
LOOKUP defaulted `source_id`/`source_version` to independent bare
literals (`"yfinance_daily"`/`"1.0.0"`), completely disconnected from
`price_path_acquisition.SOURCE_VERSION` — the constant every acquisition
and the report/outbox identity (`_price_path_target_identity`) actually
used. Both happened to read `"1.0.0"` and so happened to agree, but
nothing enforced that agreement. Combined with the prior pass's new
load-bearing manifest validator, this meant: a legacy or incompatible
row found by the lookup, rejected by manifest validation, would be
found again by the SAME stale lookup on every retry — permanently
blocked, never able to fall through to fresh acquisition under a
genuinely new identity.

### Fix

New `services/postmortem/price_path_identity.py` — the one leaf every
price-path module imports version constants from; zero imports INTO it
from any other price_path_* module, so the import direction is safe.
`load_generation_context`'s literal defaults are removed; its three
version parameters now default to live references into `CURRENT_PRICE_
PATH_SOURCE_IDENTITY`, the exact same object `_price_path_target_
identity()` reads from. `SOURCE_VERSION` bumped `1.0.0` → `1.1.0`.

### Legacy/current compatibility semantics (Stage 4)

- **Old 1.0.0 row**: remains immutable and readable; simply no longer
  matched by the 1.1.0 lookup (never "rejected then retried forever" —
  it is structurally invisible to the current identity's search, which
  is a stronger and simpler guarantee than "rejected but not blocking").
  Current acquisition proceeds fresh under 1.1.0.
- **Current 1.1.0 valid row**: compatible replay, zero provider calls,
  manifest verified before calculation.
- **Current 1.1.0 corrupt row**: fails closed exactly as before —
  `MANIFEST_INTEGRITY_VIOLATION`, no provider call, no calculator, no
  new evidence under the same identity, no report, original row
  unchanged — proven with a legacy 1.0.0 row ALSO present, to confirm
  the two failure modes don't interact
  (`TestCurrentVersionCorruptManifestFailsClosedAlongsideLegacyRow`).
- **No current row**: normal fresh acquisition under 1.1.0.

### Complete manifest contract (Stage 5)

Expanded from 16 to 30 required fields plus 13 cross-checks (exact list
in the "Current Architecture" section above). Also now validates a
FRESHLY BUILT bundle's own manifest before persistence, not only a
replayed row's — proven self-consistent for both US and IN markets.

`verify_source_manifest_integrity`'s docstring corrected to state its
actual security boundary honestly: an ordinary SHA-256 self-consistency
check (corruption/drift detection), not cryptographic authentication —
it provides no protection against an attacker who can rewrite both the
manifest content and its stored hash together.

### India and US real-PostgreSQL assurance (Stage 6)

| Requirement | Test |
|---|---|
| India symbol normalization (RELIANCE → RELIANCE.NS) | `TestIndiaCompleteAcquisition::test_india_acquisition_persists_correct_manifest_and_identity` |
| India timezone/session (Asia/Kolkata) | same test — `market_timezone == "Asia/Kolkata"` |
| India acquisition (full lifecycle, manifest verifies) | same test |
| India replay (zero provider calls, idempotent) | `TestIndiaEvidenceReplay::test_india_true_evidence_replay_then_report_replay` |
| US symbol normalization (AAPL, no suffix) | `TestUSCompleteAcquisition::test_us_acquisition_persists_correct_manifest_and_identity` |
| US timezone/DST (America/New_York, explicit DST-date case) | same test, `use_dst_date=True` parametrization |
| US acquisition (full lifecycle, manifest verifies) | same test |
| US replay | `TestUSEvidenceReplay::test_us_true_evidence_replay_then_report_replay` |
| Cross-market isolation | `TestMarketIsolation::test_india_and_us_evidence_remain_isolated` |

### Legacy coexistence real-PG tests (Stage 7)

| Requirement | Test |
|---|---|
| Legacy 1.0.0 row + fresh 1.1.0 acquisition, both persist, old unchanged | `TestLegacyAndCurrentEvidenceCoexistence::test_legacy_row_does_not_block_fresh_current_acquisition` |
| Legacy 1.0.0 report + new 1.1.0 report coexist, distinct identities | `TestLegacyAndCurrentEvidenceCoexistence::test_genuine_legacy_price_path_report_and_current_report_coexist` |
| Legacy/current outbox rows coexist, current request claims only 1.1.0 | `TestLegacyAndCurrentEvidenceCoexistence::test_genuine_legacy_price_path_outbox_and_current_outbox_coexist` |
| Valid current 1.1.0 replay, no duplicate evidence | `TestUSEvidenceReplay` / `TestIndiaEvidenceReplay` (already covers this) |
| Corrupt current 1.1.0 manifest with legacy row present | `TestCurrentVersionCorruptManifestFailsClosedAlongsideLegacyRow::test_corrupt_current_manifest_fails_closed_with_legacy_row_present` |
| Legacy row alone does not become the replay candidate | `TestLegacyAndCurrentEvidenceCoexistence::test_legacy_row_does_not_block_fresh_current_acquisition` (same test proves this — acquisition_status is ACQUISITION_REQUIRED, never COMPATIBLE_REPLAY, with only the legacy row present) |
| Concurrent 1.1.0 acquisition uniqueness | not independently re-tested — a property of the outbox's own `ON CONFLICT DO NOTHING` unique-index mechanics, unchanged by this pass; already covered by the existing, retained direct-outbox-contention tests |

### Local performance reconciliation (Stage 8)

The prior pass's local non-PG run reported 2728.64s versus this
codebase's normal ~90-100s range. Re-run this pass under `time -l` with
`--durations=30`: **96.84s wall time** (4753 passed, 0 failed) —
does NOT reproduce the slowdown. System evidence at investigation time:
`uptime` showed the machine had been up only ~20-22 minutes (a very
recent reboot — consistent with why this session's own `/tmp` worktree
had also been cleared) with 15-minute load average 22.99 against 10
CPU cores (`sysctl hw.ncpu`), well over 2x core count, alongside active
Chrome renderer/GPU-process and system-daemon CPU usage. Honest
conclusion: no direct evidence ties the SPECIFIC 2728s run to a
specific cause (load average was not captured at the time that run
happened), but the machine's own state around that time — a very
recent reboot with unusually high load averages — is a genuine,
directly-observed condition consistent with (not proof of) transient
system contention; the slowdown itself did not reproduce on retry, and
green real-PostgreSQL CI results for that pass are not invalidated by
it.

### Local verification

`pytest backend/tests -m "not postgres_integration" --durations=30` →
**4753 passed, 0 failed, 175 deselected**, 96.84s. Log at
`/tmp/sprint3a-stage-j3-versioned-identity.log`.

### Real-PG collection: 175 (up from 165)

### Remaining Stage J scope (unchanged from the prior pass, explicitly restated)

Stage J is **not** complete. The following remain, in dependency order:

- Observed-touch-pattern separation from governed-touch-order
  conclusion (`classify_touch_order` still returns one collapsed enum).
- Governed touch-order redesign (the four-tier hierarchy this and prior
  passes' checkpoint reports describe).
- Single-sided partial-boundary-touch policy change (currently a
  single-sided boundary touch stays a definitive `TARGET_ONLY`/
  `STOP_ONLY`; the requested conservative policy would make it
  `BOUNDARY_BAR_AMBIGUOUS` instead).
- Live persisted ambiguity proof through the real endpoint (blocked on
  the two items above — the live endpoint's own hardcoded
  `level_history_complete=False` currently makes `BOTH_SAME_BAR_
  AMBIGUOUS`/`BOUNDARY_BAR_AMBIGUOUS` unreachable through `/generate`
  directly for any real trade).
- Traceability-validator semantic expansion (test-node-existence
  cross-check via a deterministic repository test index, per-row
  semantic assertions beyond rows 13/31/33, stale-name rejection for
  future refactors).

### Final status (updated)

Implemented on feature branch only; locally verified (4753 passed);
PG15/PG17 verification pending this pass's own CI dispatch (see
checkpoint report); not PR-reviewed; not merged; not deployed; all
three feature flags remain disabled.

## Stage J3A — True Both-Market Evidence Replay, Current-Trade Compatibility, Legacy Identity Assurance (this pass)

Closes the remaining Stage J3 assurance contradictions the prior pass's
own checkpoint report honestly did not close.

### Distinguishing fresh acquisition, evidence replay, and report replay

- **Fresh acquisition**: no compatible evidence exists; the live
  provider is called; a new evidence row and a new report are both
  created in the same request.
- **Evidence replay**: compatible evidence already exists, but no
  current-identity report exists yet; the provider is NOT called; a
  report is built from the persisted evidence
  (`acquisition_status=COMPATIBLE_REPLAY`, `provider_call_expected=
  False`, `reused_evidence_id` set to the existing row's own id).
- **Report replay**: a current-identity report already exists;
  `_attempt_price_path_enhancement` returns it immediately
  (`existing_pp_report is not None`) — this short-circuits BEFORE any
  evidence lookup, outbox claim, or manifest check even runs.

The prior pass's India/US "evidence replay" tests actually proved report
replay on their second `/generate` call, since their first call already
created BOTH the evidence row and the report in one step (fresh
acquisition). Corrected this pass — see the traceability table below.

### Current-trade replay compatibility

New `price_path_acquisition.validate_replay_compatibility` — delegates
first to `validate_manifest_compatibility` (internal self-consistency),
then compares the evidence row against the CURRENT persisted trade's own
facts (trade ID, user ID, symbol, market, deterministic provider-symbol
normalization, market timezone, entry/exit timestamps, requested window
start/end). A row that is internally perfectly consistent but describes
a different trade's market/symbol/window now fails closed under the
distinct `REPLAY_TRADE_CONTEXT_MISMATCH` reason code — proven for both
directions (India evidence rejected for a US trade, US evidence rejected
for an India trade) and for wrong-symbol-same-market. Never consults the
current stock universe; never infers a rename/merger/delisting.

### Complete manifest semantic contract (remaining Stage 5 items)

Added semantic-VALUE checks (not merely field presence) for `provider`,
`timezone_behavior`, `raw_ohlc_basis_claim`, `acquisition_mode` (each
must equal its exact pinned constant), and
`requested_trading_weekday_count` (must be a non-negative integer
matching the deterministic count for the persisted window).

### Genuine legacy price-path identity coexistence

The prior pass's "legacy report/outbox coexistence" tests only proved a
Sprint 2 (`report_schema_version=1.0.0`) row coexists with the 1.1.0
price-path row — those never shared a schema version, so coexistence was
never actually at risk. This pass seeds a GENUINE old price-path
report/outbox row (`report_schema_version=1.1.0`,
`calculation_version` containing `price_path:1.0.0+src:1.0.0` — the
exact identity a real pre-Stage-J3 acquisition would have produced) and
proves the real collision boundary the unique index enforces: both rows
coexist, the old row is untouched, the current row's identity contains
`src:1.1.0`, and the supersession chain still points at the live path's
own always-Sprint-2-identity `prior_report` lookup (confirmed this is
correct behavior, not a bug — an old price-path report coexisting
alongside is never mistaken for the supersession target).

### Traceability (exact test references)

| Requirement | Test |
|---|---|
| India true evidence replay | `TestIndiaEvidenceReplay::test_india_true_evidence_replay_then_report_replay` |
| India report replay (same test, second `/generate` call) | same test |
| US true evidence replay | `TestUSEvidenceReplay::test_us_true_evidence_replay_then_report_replay` |
| US report replay (same test, second call) | same test |
| Wrong-market (IN evidence, US trade) rejection | `TestWrongMarketReplayCandidateRejected::test_wrong_market_in_evidence_rejected_for_us_trade` |
| Wrong-market (US evidence, IN trade) rejection | `TestWrongMarketReplayCandidateRejected::test_wrong_market_us_evidence_rejected_for_india_trade` |
| Wrong-symbol rejection | `TestWrongSymbolReplayCandidateRejected::test_wrong_symbol_evidence_rejected` |
| Genuine old/current price-path report coexistence | `TestLegacyAndCurrentEvidenceCoexistence::test_genuine_legacy_price_path_report_and_current_report_coexist` |
| Genuine old/current price-path outbox coexistence | `TestLegacyAndCurrentEvidenceCoexistence::test_genuine_legacy_price_path_outbox_and_current_outbox_coexist` |
| Current-trade compatibility decision (unit) | `TestReplayCompatibilityAgainstCurrentTrade` (9 tests, `test_price_path_acquisition_boundary.py`) |

### Still not done this pass (honestly, not silently dropped)

- **India exact 09:15/15:30 IST session-boundary real-PG proof** and the
  symmetric **US exact 09:30/16:00 ET (+ DST) proof** — `ENTRY_BAR_
  INCLUDED_FULL`/`EXIT_BAR_INCLUDED_FULL` policy classification is
  exercised indirectly by the existing acquisition tests (which use
  whatever "now" the test run happens to produce, or a forced non-exact
  window for the DST case) but not proven against a trade window forced
  to the EXACT official open/close timestamp through the real endpoint.
  This remains open for a dedicated follow-up.
- A partial-session `PARTIAL_UNKNOWN` real-PG proof for each market,
  same reason.

### Local verification

`pytest backend/tests -m "not postgres_integration" --durations=30` →
**4762 passed, 0 failed, 178 deselected**, 93.01s. Log at
`/tmp/sprint3a-stage-j3a-assurance.log`.

### Remaining Stage J scope (unchanged, restated)

Stage J is **not** complete. Observed-touch-pattern separation from
governed-touch-order conclusion, the governed touch-order redesign,
single-sided partial-boundary-touch policy, live persisted ambiguity
proof, and traceability-validator semantic expansion all remain — none
started this phase, per the explicit instruction not to begin
touch-semantics work. India/US exact session-boundary real-PG proof
(above) also remains, newly identified as open by this pass's own
honest accounting.

---

## Stage J3B.1 — India/US session-boundary characterization and
## real-PG assurance (results-only addendum)

TEST-ONLY. Closes the "still not done this pass" gap recorded directly
above (India/US exact-boundary and partial-session real-PG proof) and
adds DST-adjacent-session, early-close, naive-timestamp, Muhurat, and
PostgreSQL timestamp-awareness characterization. No production code
changed. Architecture is unchanged; no fix is promised or implied by
this addendum. Stage J remains **not** complete — see "Remaining Stage
J scope" above, still fully outstanding.

### Regular India boundary — proven

Real-PG, via the actual `/generate` endpoint, explicit `opened_at`/
`closed_at` overrides (never uncontrolled wall-clock), deterministic
provider fixtures:

- Exact 09:15 IST entry / exact 15:30 IST exit →
  `ENTRY_BAR_INCLUDED_FULL` / `EXIT_BAR_INCLUDED_FULL`, persisted
  `market=IN`, `provider_symbol=RELIANCE.NS`,
  `market_timezone=Asia/Kolkata`, manifest integrity verified, full
  evidence/report lifecycle succeeds
  (`TestIndiaRealPGBoundaryProof::test_india_exact_full_session_boundary`).
- One-second-after-open entry / one-second-before-close exit →
  both `PARTIAL_UNKNOWN`, no fabricated full-boundary claim
  (`test_india_partial_session_one_second_after_open`).
- Confirmed repository holiday (`2026-01-15`, `NSE_EXTRA_HOLIDAYS`) →
  `PARTIAL_UNKNOWN`, non-trading-day limitation present, no crash, no
  live calendar lookup (`test_india_holiday_characterization`).

### Regular US boundary — proven

Same structure and guarantees, US market:

- Exact 09:30 ET entry / exact 16:00 ET exit → both `INCLUDED_FULL`,
  persisted `market=US`, unsuffixed `provider_symbol=AAPL`,
  `market_timezone=America/New_York`
  (`TestUSRealPGBoundaryProof::test_us_exact_full_session_boundary`).
- One-second-after-open / one-second-before-close → both
  `PARTIAL_UNKNOWN`
  (`test_us_partial_session_one_second_after_open_and_before_close`).

### DST-adjacent trading-session proof

2026 spring (`2026-03-08`) and autumn (`2026-11-01`) transition dates
determined programmatically via `zoneinfo` (never hardcoded, never
assumed from memory) in both the unit and real-PG test files
independently. The transition Sunday itself is never used as a
session-boundary test, since the US market is closed that day. For the
last trading session before and first trading session after each
transition: 09:30 ET entry / 16:00 ET exit both still resolve to
`INCLUDED_FULL`, and the persisted UTC offset genuinely differs across
the transition as `ZoneInfo` determines (EST↔EDT), not a hardcoded
assumption
(`TestUSRealPGBoundaryProof::test_us_dst_adjacent_trading_sessions[spring|autumn]`,
`TestDSTAssuranceStageJ3B1` unit tests). A separate, explicitly-labeled
PURE timezone characterization (not a market-session test) proves
`fold=0`/`fold=1` resolve to distinct UTC instants in the autumn
ambiguous hour; no production code currently constructs times in that
window, so this does not by itself justify a production change.

### Early-close — remains a KNOWN_LIMITATION, not supported

Confirmed unchanged: `resolve_session` always uses the standard close
(16:00 US / 15:30 IST) regardless of any real early-close calendar;
`EARLY_CLOSE_UNSUPPORTED` is present on every session resolution; a
time after a real (unmodeled) early close but before the standard
close is classified as ordinary intraday (`PARTIAL_UNKNOWN`), never as
`EARLY_CLOSE_SUPPORTED`
(`TestEarlyCloseKnownLimitationStageJ3B1`). No real early-close date
was hardcoded — none is present as an authoritative deterministic
repository fixture.

### Muhurat/special-session — remains unsupported, now characterized

`MUHURAT_SESSIONS` (`services/market_hours.py`) is confirmed consulted
ONLY by `is_market_open()` (the live "is market open right now" check)
— `session_boundary.py` never references it (proven by source-text
inspection, not inference). The one deterministic repository entry
(`2026-11-08`) is itself a Sunday in this codebase's own calendar, so
price-path classification treats it as an ordinary non-trading day
(`PARTIAL_UNKNOWN`), with no awareness that a real, brief Muhurat
session occurs on it
(`TestMuhuratSpecialSessionInventoryStageJ3B1`). Not implemented this
phase, per instruction.

### Naive-timestamp API characterization

- `session_boundary.classify_entry_boundary`/`classify_exit_boundary`
  reject naive input with `SessionBoundaryError` (confirmed, unit).
- `price_path_acquisition._resolve_boundary_policies` catches that
  error and returns `PARTIAL_UNKNOWN` for both sides without
  propagating a typed failure or any limitation string distinguishing
  "naive input" from "genuinely ambiguous timestamp" (confirmed, unit —
  refines the Stage J3B investigation's framing of this as silent).
- **Refined finding**: the final `PricePathEvidenceBundle` itself DOES
  reject a naive `entry_timestamp`/`exit_timestamp` in its own
  `__post_init__` (`price_path_evidence.py`), so acquisition ultimately
  fails closed for a naive-timestamp call end-to-end — it does not
  silently persist wrong-window evidence. This corrects the read-only
  investigation's original framing, which had not yet traced execution
  all the way to bundle construction.
- **Confirmed, precise host-TZ dependency**: before that final
  rejection, the entry_date/exit_date used to build the OUTBOUND
  provider fetch window (`entry_timestamp.astimezone(market_tzinfo)`)
  is computed from the naive timestamp using the HOST PROCESS
  timezone, and a real provider call would go out with a
  host-TZ-dependent, wrong window before the request is ultimately
  discarded — proven by controlling `TZ` via `time.tzset()` under two
  extreme host timezones and observing two different captured fetch
  windows for the identical naive input, then restoring the original
  TZ (`test_naive_timestamp_fetch_window_depends_on_host_tz_before_final_rejection`).

### PostgreSQL timestamp-awareness invariant — holds today

`paper_trades.opened_at`/`closed_at`, read back from a real PostgreSQL
row through the actual endpoint, are always timezone-aware
(`tzinfo is not None` for both) —
`TestPostgresTimestampAwarenessInvariant::test_persisted_paper_trade_timestamps_are_always_timezone_aware`,
passing on both PG15 and PG17. This distinguishes the naive-timestamp
gap above as a **confirmed defect in the reusable acquisition API**
(reachable only if a future caller passes a naive timestamp directly),
**not a proven production endpoint exposure** — nothing in this
addendum demonstrates the real endpoint can currently produce or pass
along a naive timestamp.

### Verification

Local: `pytest backend/tests -m "not postgres_integration"` →
**4793 passed, 0 failed, 186 deselected**, 92.83s.
Real PostgreSQL (`backend_postgres_integration.yml`, run
`30524175022`): **PG15 — 186 passed, 0 failed**; **PG17 — 186 passed,
0 failed** (both up from the prior 178-test baseline by exactly the 8
new real-PG tests added this phase; all prior replay, manifest, and
concurrency tests retained and still passing).

### Explicit non-claims

This addendum does not mark Stage J complete, does not promise or
schedule a specific production fix, and does not change the
architecture described earlier in this document. The confirmed
naive-timestamp acquisition-API defect is flagged for a future,
separately-approved production-fix stage — not fixed here.

---

## Stage J3B.2 — naive-timestamp pre-I/O validation guard (targeted
## production correction)

One narrowly approved production correction, closing exactly the
confirmed defect Stage J3B.1 flagged above. No other production
semantics changed.

### The characterization result being corrected

Stage J3B.1 confirmed that a naive entry/exit timestamp reached
`entry_timestamp.astimezone(market_tzinfo)` inside both
`acquire_price_path_evidence` and `build_price_path_evidence`, and that
the resulting `entry_date`/`exit_date` (the actual outbound provider
request window) was genuinely host-process-timezone-dependent, before
`PricePathEvidenceBundle.__post_init__` eventually rejected the naive
value in its own final validation.

### The correction

A new pure helper, `_require_timezone_aware_trade_timestamps(entry_timestamp,
exit_timestamp)` (`services/postmortem/price_path_acquisition.py`), is
called at the very start of both `acquire_price_path_evidence` and
`build_price_path_evidence` — before any `.astimezone()` call,
provider-symbol calculation, bounded-window calculation, manifest
construction, boundary-policy resolution, or provider I/O
(`fetch_bars_fn`/`fetch_splits_fn`/`fetch_dividends_fn`). It rejects a
value that is not a `datetime`, whose `tzinfo is None`, or whose
`tzinfo.utcoffset()` returns `None` (a pathological but real case
under the `datetime` contract) — raising `PricePathEvidenceError` with
a sanitized message identifying only `entry_timestamp` or
`exit_timestamp` by name, never the raw value. This is an
input-contract guard, not timestamp repair: it never assumes UTC, IST,
ET, or the host timezone, never attaches a timezone, and never converts
a naive value. `_resolve_boundary_policies`'s own existing behavior
(catching `SessionBoundaryError` and returning `PARTIAL_UNKNOWN` when
called directly and in isolation) is unchanged and still separately
characterized by its own unit test — the production acquisition
functions simply never reach it with a naive timestamp after this
correction, since the guard now runs first.

The existing generic `except Exception` handler in
`_attempt_price_path_enhancement` (`api/routers/paper_trading.py`)
already catches `PricePathEvidenceError` (it was never limited to
`PriceProviderAcquisitionError`) and settles it exactly like any other
internal failure — sanitized error summary (`type(exc).__name__` only,
never the raw naive timestamp), `PRICE_PATH_FAILED_RETRYABLE`, no
report, no evidence row. No change to that handler was needed.

### Regular India/US session-boundary semantics — unchanged

No change to `session_boundary.py`, `resolve_session`, `classify_entry_
boundary`, `classify_exit_boundary`, `classify_same_day_trade`, or any
DST/holiday/weekend calendar logic. All Stage J3B.1 unit and real-PG
boundary tests (India exact/partial, US exact/partial, DST-adjacent
spring/autumn) pass unchanged against the corrected code.

### Defence in depth — final bundle validation retained

`PricePathEvidenceBundle.__post_init__`'s own naive-timestamp rejection
(`price_path_evidence.py`) is NOT removed or weakened. It remains a
second, independent check — this correction adds a pre-I/O guard in
front of it, it does not replace it.

### Versioning decision — no version bumped

`SOURCE_VERSION`, `EVIDENCE_BUNDLE_SCHEMA_VERSION`,
`SOURCE_MANIFEST_SCHEMA_VERSION`, `BOUNDARY_POLICY_VERSION`,
calculation-rules version, and report schema version are all
unchanged. Rationale: this change does not alter any valid acquired
evidence, persisted shape, session-boundary policy, or calculation
semantics for a properly timezone-aware caller — every existing
passing test (unit and real-PG) continues to pass byte-for-byte
against the same manifest/evidence shape. It only moves the rejection
of already-invalid input (a naive timestamp, which the bundle's own
constructor already rejected) to the correct point before provider I/O
runs. There is no persisted-evidence shape or replay-compatibility
contract for a valid trade that this correction changes.

### Early-close, Muhurat — unchanged, still unsupported

No change. Both remain `KNOWN_LIMITATION`, exactly as characterized in
the Stage J3B.1 addendum above.

### DST-adjacent tests — unchanged, still passing

The Stage J3B.1 spring/autumn DST-adjacent real-PG and unit tests are
untouched and continue to pass against the corrected acquisition code
(they use genuinely aware timestamps throughout, so the new guard never
rejects them).

### PostgreSQL endpoint timestamps — still timezone-aware

The Stage J3B.1 PostgreSQL timestamp-awareness invariant
(`paper_trades.opened_at`/`closed_at` always `tzinfo is not None`
through the real endpoint) is re-verified unchanged by this phase's
real-PG forced-naive test (Stage 6): the REAL persisted trade
timestamps are never altered — only the in-memory
`GenerationContext.entry_timestamp`/`exit_timestamp` returned by a
test-only monkeypatched `load_generation_context` is mutated, to
simulate a future internal caller violating the contract.

### The fixed reusable-API risk is not evidence of a prior live-endpoint defect

This correction closes a defect in the *reusable acquisition API*
(`acquire_price_path_evidence`/`build_price_path_evidence`), reachable
only if a caller passes a naive timestamp directly. The Stage J3B.1
PostgreSQL timestamp-awareness invariant (unchanged, re-verified above)
already established that the real `/generate` endpoint has never been
shown to produce or pass along a naive timestamp. Fixing the reusable
API's input contract is not evidence that the live endpoint was ever
exposed to this defect — it closes the risk pre-emptively, for any
future internal caller.

### Verification

Local: `pytest backend/tests -m "not postgres_integration"` →
**4804 passed, 0 failed, 187 deselected**, 95.74s. Log at
`/tmp/sprint3a-stage-j3b2-naive-timestamp-guard.log`.

### Explicit non-claims

This correction does not mark Stage J complete, does not implement
early-close or Muhurat support, does not change touch-semantics, and
does not change any evidence/report calculation. It is scoped to
exactly one input-contract guard.

---

## Stage J4B — Observed numerical-crossing and session-attribution core

**IMPLEMENTED, UNIT-TESTED, NOT REPORT-WIRED, NOT PERSISTED, NOT
ENDPOINT-VERIFIED.** Purely additive internal model in
`services/postmortem/price_path_calculator.py`. Nothing above the new
section is changed; `price_path_generation.py`, `price_path_claims.py`,
and `paper_trading.py` do not import or reference anything below it —
`TouchResult`, `detect_touches`, `classify_touch_order`,
`build_touch_order_claim`, and every persisted report/claim/evidence-item
shape remain byte-for-byte unchanged, proven by dedicated regression
assertions (see Verification below).

### The new observed numerical-crossing model

Describes ONLY what immutable daily OHLC bars prove about two SUPPLIED
numerical values — deliberately never "stop"/"target LEVELS" being
active, never "touched" in the governed sense, never that the trade
should have closed. Terminology throughout: "numerical value,"
"crossed," "crossing observation," "safely attributable crossing,"
"partial-boundary observation" — never "touch" as the primary term (the
two new dataclasses' own field names are structurally proven never to
contain the substring "touch").

### Session-attribution contract

`classify_bar_session_attribution(bundle, bar)` — pure, classifies a
single bar's `session_date` against ONLY
`bundle.requested_window_start`/`requested_window_end` and
`bundle.entry_bar_policy`/`exit_bar_policy`. Never consults `bars[0]`,
`bars[-1]`, `observed_window_start`/`end`, or raw array position — this
is the direct fix, for this new layer only, of the Stage J4A finding #2
defect (`detect_touches`'s own array-position heuristic is unchanged
and untouched). Seven typed attribution values:
`INTERIOR`, `ENTRY_INCLUDED_FULL`, `EXIT_INCLUDED_FULL`,
`SAME_DAY_INCLUDED_FULL`, `ENTRY_PARTIAL_UNKNOWN`,
`EXIT_PARTIAL_UNKNOWN`, `SAME_DAY_PARTIAL_UNKNOWN`. Fails closed
(`SessionAttributionError`) on any unrecognized boundary-policy value —
never silently treated as interior.

### First observed versus first safely attributable crossing

`observe_numerical_level_crossing(bundle, supplied_level_value,
level_kind)` retains BOTH independently (Stage J4A finding #5): the
first crossing of any kind anywhere in the bundle, and the first later
crossing whose session attribution is safely attributable (`INTERIOR`
or any `INCLUDED_FULL`). An earlier crossing observed only in a
`PARTIAL_UNKNOWN` boundary bar is never discarded merely because a
later safely-attributable bar also crosses the same value —
`partial_boundary_crossing_observed` stays `True` in that case even
though a `first_safely_attributable_*` basis was found elsewhere.
Proven directly by dedicated tests for both orderings (partial-then-
safe and safe-then-partial).

### INCLUDED_FULL versus PARTIAL_UNKNOWN handling

`INTERIOR` and any `INCLUDED_FULL` attribution (entry, exit, or
same-day) is safely attributable to the holding period; any
`PARTIAL_UNKNOWN` attribution is observed but never safely
attributable — `is_safely_attributable_session()` encodes this
directly, exercised across the full unit matrix including exact-open,
exact-close, and same-day full/partial sessions.

### No dependency on level history

`observe_numerical_level_crossing` and
`summarize_observed_numerical_crossings` accept no level-history
parameter of any kind — proven structurally via `inspect.signature` in
the test suite, not merely by omission at call sites. The observed-
crossing summary's allowed pattern vocabulary (`NO_NUMERICAL_VALUES_
SUPPLIED`, `NEITHER_NUMERICAL_VALUE_CROSSED`,
`TARGET_VALUE_ONLY_CROSSED`, `STOP_VALUE_ONLY_CROSSED`,
`BOTH_VALUES_SAME_BAR`, `TARGET_VALUE_BAR_BEFORE_STOP_VALUE_BAR`,
`STOP_VALUE_BAR_BEFORE_TARGET_VALUE_BAR`, and the parallel
safely-attributable set) deliberately never uses `NO_LEVELS_CONFIGURED`,
`TARGET_ONLY`, `STOP_ONLY`, `TARGET_BEFORE_STOP`, or `STOP_BEFORE_TARGET`
— those remain governed touch conclusions reserved for the corrected
J4C/J4D contract established in the Stage J4A.1 addendum above.

### No report wiring, no persistence

`price_path_calculator.py` is structurally proven (source-text
inspection) never to import `price_path_generation` or
`price_path_claims`; those two modules and `paper_trading.py` are
structurally proven never to reference any J4B symbol
(`observe_numerical_level_crossing`,
`summarize_observed_numerical_crossings`,
`classify_bar_session_attribution`,
`NumericalLevelCrossingObservation`, `ObservedNumericalCrossingSummary`).
Nothing is persisted to `structured_report`, no new claim or
evidence-item type is emitted through the live path, and the API
response/frontend output are unaffected.

### No version bump

`SOURCE_VERSION`, `EVIDENCE_BUNDLE_SCHEMA_VERSION`,
`SOURCE_MANIFEST_SCHEMA_VERSION`, `BOUNDARY_POLICY_VERSION`,
`CALCULATION_RULES_VERSION`, and `PRICE_PATH_REPORT_SCHEMA_VERSION` are
all unchanged, confirmed by a dedicated regression assertion pinning
their exact current values. Rationale: J4B creates a new internal
deterministic observation model but does not persist, expose, or
consume it anywhere in the live report path — there is no persisted
shape, calculation output, or API contract for a real trade that this
phase alters in any way.

### J4C remains blocked; J4D remains separate

J4C (versioned report fields + governed conclusion) remains blocked
pending the corrected governed-conclusion contract from the Stage
J4A.1 addendum above — specifically:

1. Absent values at both endpoints (entry and exit) do not prove that
   no level existed throughout the holding period — `NO_LEVELS_
   CONFIGURED_THROUGHOUT` is a distinct, stronger claim than "not
   present in the two snapshots we happen to have."
2. A present snapshot containing `NULL` for a stop/target field is not
   itself missing evidence — it may be a genuine, intentionally-absent
   level, not a data gap.
3. `NO_LEVELS_CONFIGURED_THROUGHOUT` remains unreachable before J4D:
   proving a level was never configured across the ENTIRE holding
   period (not just at the two snapshot endpoints) requires the same
   kind of governed write-invariant J4D is scoped to establish for
   `levels_modified_after_entry` — it cannot be inferred from the
   endpoints-only evidence available today.

J4D (the separate, later write-invariant correction enabling
`VERIFIED_UNCHANGED_THROUGHOUT`) remains untouched and unscheduled by
this phase.

### Verification

Local: `pytest backend/tests -m "not postgres_integration"` →
**4860 passed, 0 failed, 187 deselected**, 120.43s. Log at
`/tmp/sprint3a-stage-j4b-observed-crossing.log`. Dedicated new test
file `tests/unit/test_price_path_observed_crossing.py` (56 tests): the
full session-attribution algorithm, the required 30-item crossing-
detection matrix (India and US, exact-boundary, partial-boundary,
gap-through, missing provider bars, fail-closed unrecognized policy),
and Stage 8 compatibility assertions proving `detect_touches`/
`classify_touch_order`/`build_price_path_report_payload` outputs,
the price_path rule registry, and all six version constants are
unchanged. No existing unit test was weakened, deleted, or had its
expected output rewritten.

PostgreSQL: this phase adds unit tests only — no real-PG test was
added or modified, and the real-PostgreSQL collection is expected to
remain at 187 (unchanged from the Stage J3B.2 baseline).

### Explicit non-claims

Stage J is **not** complete. This phase does not begin J4C, does not
begin J4D, does not change any report output, and does not change any
production behavior reachable through the live `/generate` endpoint.

---

## Stage J4B.1 — Observed-crossing contract, context-identity and
## invariant hardening

**NOT REPORT-WIRED, NOT PERSISTED, NOT ENDPOINT-WIRED, NOT MERGE-
AUTHORIZED, NOT DEPLOYMENT-AUTHORIZED.** Hardens the additive,
unpublished, unpersisted, unconsumed J4B internal model from the
section above. Confirmed all twelve items from the Stage 1 defect-
surface inspection were present at the starting SHA and corrected
every one; none was already resolved.

### Finite-positive supplied-value contract

`_validate_supplied_numerical_value(supplied_level_value, level_kind)`
validates `level_kind` first, then the value: `None` remains valid (no
value supplied); `bool` is rejected explicitly (never silently coerced
to 0/1); only `int`/`float` are accepted and an `int` is deterministically
converted to `float`; `NaN`, positive/negative infinity, positive/negative
zero, and every negative value are rejected; a valid value is always a
finite, strictly positive `float`. Never clamps, rounds, repairs,
replaces, or infers an invalid value; never infers currency or tick
size; imposes no arbitrary maximum.

### Strict GAP_THROUGH semantics

`bar.open` exactly equal to the supplied value is now `NORMAL`, not
`GAP_THROUGH`, for both `TARGET_VALUE` (`open > value` required for
`GAP_THROUGH`) and `STOP_VALUE` (`open < value` required). Crossing
itself remains inclusive (`high >= value` / `low <= value`).

### Complete boundary-policy prevalidation

`classify_bar_session_attribution` validates the requested-window
ordering and BOTH `entry_bar_policy`/`exit_bar_policy` values up front,
before deciding interior/entry/exit/same-day — an unrecognized policy
on either side always fails closed (`SessionAttributionError`), even
when the other side is a recognized value, including every same-day
combination. `is_safely_attributable_session` now raises on an unknown
attribution value instead of silently returning `False`.

### Every-bar attribution before crossing calculation

`observe_numerical_level_crossing` calls `classify_bar_session_
attribution` for every bar in the bundle BEFORE evaluating whether any
bar crosses the supplied value — an out-of-window or invalid-policy
bar fails closed even if it would never have crossed anything.

### Reliance on the existing immutable chronological bundle invariant

`observe_numerical_level_crossing` no longer calls `sorted()` on
`bundle.bars` — it iterates the bundle's own already-enforced stored
order. `PricePathEvidenceBundle.__post_init__` already rejects
duplicate-date and out-of-order bars; re-sorting here would only mask
an upstream evidence-contract violation instead of surfacing it.

### Immutable observation context and anti-mixing purpose

A new frozen `NumericalCrossingObservationContext` — built ONLY from
the exact `PricePathEvidenceBundle` passed to
`observe_numerical_level_crossing` — is attached to every observation,
including no-value and no-crossing ones. Two observations can never be
combined by `summarize_observed_numerical_crossings` unless their
contexts are exactly equal, which requires an identical trade, symbol,
market, evidence bundle version/source identity, evidence hash,
manifest integrity hash, bar interval, price-adjustment basis, market
timezone, requested window, and both boundary policies.

### Context fields and deliberate exclusion of user_id

Fields: `paper_trade_id`, `symbol`, `market`, `evidence_bundle_version`,
`source_id`, `source_version`, `evidence_hash`,
`source_manifest_integrity_hash`, `bar_interval`,
`price_adjustment_basis`, `market_timezone`, `requested_window_start`/
`end`, `entry_bar_policy`, `exit_bar_policy`. Deliberately excludes
`user_id`, current quotes, and stop/target values, and derives nothing
externally. **`evidence_hash` and `source_manifest_integrity_hash`
provide deterministic identity and corruption/drift association ONLY —
neither is described as, or functions as, cryptographic authentication
of anything.**

### First observed, first safe, first partial retention

`NumericalLevelCrossingObservation` now retains three independent
bases — `first_observed_*`, `first_safely_attributable_*`, and the new
`first_partial_boundary_*` — proven for both orderings (partial crossing
first then a later safe crossing; safe crossing first then a later
partial crossing) to retain all applicable bases simultaneously, never
discarding one because another was found.

### Observation dataclass invariants

`NumericalLevelCrossingObservation.__post_init__` validates: no-value
observations carry no group fields and report no crossing; no-crossing
observations (value supplied, nothing crossed) likewise carry no group
fields; a crossed observation's first-observed group is fully present
and internally consistent (crossing type matches strict GAP_THROUGH
rule, session matches the group's own bar, evidence ID is canonical
and consistent with the context/session/level-kind, the bar genuinely
crosses the value, `bar.source_id` matches `context.source_id`); the
safely-attributable and partial-boundary groups are each either
completely absent or completely present, self-consistent by the same
rules, never earlier than the first-observed session, and — when
first-observed's own attribution already qualifies for that group —
required to identify the exact same bar/session/evidence-ID/crossing-
type. **Factory-only guarantee** (documented in the class's own
docstring, not merely asserted here): a standalone instance's
`__post_init__` can only validate internal self-consistency; it cannot
by itself prove the referenced bar is the chronologically earliest
qualifying crossing across the entire bundle — that guarantee comes
only from `observe_numerical_level_crossing`'s own full-bundle scan and
this module's test suite.

### Summary dataclass invariants

`ObservedNumericalCrossingSummary.__post_init__` requires
`target_observation.level_kind == TARGET_VALUE` and
`stop_observation.level_kind == STOP_VALUE` (rejecting reversed
inputs), requires byte-identical contexts on both sides (the anti-
mixing control), and recomputes both pattern fields through one pure
internal classifier (`_compute_expected_patterns`), rejecting any
forged/inconsistent stored value. `partial_boundary_observation_present`
must equal the logical OR of the two observations' own flags.

### Evidence-ID validation

Canonical format `NUMERICAL-CROSSING-{positive-trade-id}-{YYYY-MM-DD}-
{TARGET_VALUE|STOP_VALUE}`, parsed and validated by one shared
regex-based parser used both by the generator and by every observation-
invariant check — rejects malformed IDs and any trade-ID/date/level-kind
mismatch against the observation it's attached to. Contains no user ID,
symbol, market, price, provider payload, or report narrative.

### No report wiring, no persistence, no endpoint change, no version bump

Unchanged from the J4B section above — re-verified: `price_path_
calculator.py` still does not import `price_path_generation` or
`price_path_claims`; neither of those modules nor `paper_trading.py`
reference any J4B/J4B.1 symbol; no `structured_report`/claim/evidence-
item field changed; no database write includes the observation context
or summary; all six version constants (`SOURCE_VERSION`,
`EVIDENCE_BUNDLE_SCHEMA_VERSION`, `SOURCE_MANIFEST_SCHEMA_VERSION`,
`BOUNDARY_POLICY_VERSION`, `CALCULATION_RULES_VERSION`,
`PRICE_PATH_REPORT_SCHEMA_VERSION`) remain pinned unchanged by a
dedicated regression test. Adding internal context and invariant
fields to the J4B dataclasses does not authorize report use.

### J4C not started; J4D not started; Stage J not complete

Unchanged from the sections above.

### Verification

Local: `pytest backend/tests -m "not postgres_integration"` →
**4938 passed, 0 failed, 187 deselected**, 118.83s. Log at
`/tmp/sprint3a-stage-j4b1-contract-hardening.log`. New dedicated test
file `tests/unit/test_price_path_observed_crossing_invariants.py`
(78 tests) covering the value contract, session/boundary hardening,
strict gap semantics, observation context, evidence-ID validation,
observation-dataclass invariants, retention across the hardened
implementation, and summary anti-mixing, plus regression proof that
all 56 pre-existing J4B tests, `detect_touches`, `classify_touch_
order`, and all six version constants remain unchanged. This test
file covers every contract requirement group from the authorizing
prompt with representative cases; it does not mechanically enumerate
every one of the prompt's 93 numbered scenarios as a literally
separate, identically-numbered test function — several numbered items
collapse into the same underlying assertion (e.g., "reversed inputs
rejected" and "different trades rejected" are both proven by the same
context-equality check exercised from two angles) and are documented
here as covered by that shared mechanism rather than duplicated.

PostgreSQL: this phase adds unit tests only — no real-PG test was
added or modified; the real-PostgreSQL collection is expected to
remain at 187 (unchanged from the Stage J4B baseline).

### Explicit non-claims

Stage J is **not** complete. This phase does not begin J4C, does not
begin J4D, does not change any report output, and does not change any
production behavior reachable through the live `/generate` endpoint.
Evidence hashes are identity/corruption-drift associations only, never
authentication. Active stop/target history is not proven by this
phase. `NO_LEVELS_CONFIGURED_THROUGHOUT` is not claimed anywhere in
this phase's code or tests.

---

## Stage J4B.2 — Final observed-crossing contract audit, adversarial
## closure and pre-wiring certification

**NOT REPORT-WIRED, NOT PERSISTED, NOT ENDPOINT-WIRED, NOT MERGE-
AUTHORIZED, NOT DEPLOYMENT-AUTHORIZED, NOT FEATURE-FLAG-AUTHORIZED.**

### 1. What J4B originally established

The additive, internal observed-numerical-crossing model:
`NumericalLevelCrossingObservation`, `ObservedNumericalCrossingSummary`,
`classify_bar_session_attribution`, `observe_numerical_level_crossing`,
`summarize_observed_numerical_crossings`. Correct for valid,
well-typed input; never assumed adversarial or malformed input.

### 2. What J4B.1 hardened

Session-attribution algorithm correctness (window/policy prevalidation,
same-day matrix), strict `GAP_THROUGH` semantics, the immutable
`NumericalCrossingObservationContext` anti-mixing control, first-
observed/first-safe/first-partial retention, and dataclass `__post_init__`
internal-consistency invariants for both observation and summary
objects — all under the assumption that inputs were themselves
well-typed strings/dates/bools, not hostile or malformed objects.

### 3. What the independent adversarial review found after J4B.1

A dedicated Stage 1 audit reproduced **eight real, uncontrolled
exception leaks** against the exact starting SHA
(`19598256719cc931b09f163e5b01a611298e195d`) — confirmed with executed
reproduction scripts, not inferred:

1. `_validate_level_kind`'s bare `level_kind not in _VALID_LEVEL_KINDS`
   raised a raw `TypeError` for an unhashable `level_kind` (list/dict/set).
2. `_validate_supplied_numerical_value`'s `float(int)` conversion raised
   a raw `OverflowError` for an integer too large for finite float
   representation (`10**400`).
3. `observe_numerical_level_crossing` never validated its `bundle`
   argument's type — `None` raised a raw `AttributeError` on first
   attribute access.
4. `is_safely_attributable_session`'s frozenset membership checks
   raised a raw `TypeError` for an unhashable `session_attribution`.
5. `_parse_crossing_evidence_id`'s `date.fromisoformat` call raised a
   raw `ValueError` for a syntactically-plausible but calendar-
   impossible date (`2026-13-45`) — the regex constrains digit count
   only, not valid calendar ranges.
6. `NumericalCrossingObservationContext`'s `market`/policy frozenset
   checks raised a raw `TypeError` for an unhashable value.
7. `summarize_observed_numerical_crossings` accessed
   `.value_supplied`/`.crossed_anywhere` etc. on its arguments (via
   `_compute_expected_patterns`) **before** any type validation — a
   non-observation object raised a raw `AttributeError`.
8. `classify_bar_session_attribution` never validated `bundle`/`bar`
   argument types, and `_validate_boundary_policies`'s messages
   interpolated the raw invalid policy value via `!r` (a sanitization
   gap, not a crash).

No material architectural contradiction was found — all eight are
genuine, closeable defects, not requirement conflicts. The phase
proceeded past Stage 1 to correction.

### 4. What J4B.2 corrected

- One shared `_safe_str_member(value, allowed_frozenset)` guard —
  `type(value) is str and value in allowed` — replaces every bare
  `x not in some_frozenset` check in this module. Never raises for an
  unhashable input; never treats a non-`str` as a match.
- `_validate_boundary_policies`, `classify_bar_session_attribution`,
  and `observe_numerical_level_crossing` now validate `bundle`/`bar`
  argument types (`isinstance(..., PricePathEvidenceBundle/PricePathBar)`)
  before any attribute access, and validate `requested_window_start`/
  `end`/`bar.session_date` are exact `datetime.date` (not
  `datetime.datetime` subclass instances) before comparison.
- `_validate_supplied_numerical_value`'s `float()` conversion is
  wrapped; an `OverflowError` becomes `NumericalCrossingContractError`
  (`NON_FINITE_SUPPLIED_VALUE`) instead of escaping raw. The type check
  was tightened from `isinstance(x, (int, float))` (accepts subclasses)
  to `type(x) in (int, float)` (exact built-in types only, per the
  declared input-domain contract).
- `_parse_crossing_evidence_id`'s `date.fromisoformat` is wrapped; a
  `ValueError` from an impossible calendar date becomes
  `NumericalCrossingContractError` (`INVALID_CROSSING_EVIDENCE_ID`).
  Malformed evidence-ID contents are no longer echoed into the
  exception message.
- A new shared `_validate_summary_inputs(target_observation,
  stop_observation)` — validates exact `isinstance` type, level-kind
  position, and exact context equality — now runs at the **very
  beginning** of `summarize_observed_numerical_crossings`, before
  `_compute_expected_patterns` is ever called, and is reused verbatim
  inside `ObservedNumericalCrossingSummary.__post_init__` as
  defence-in-depth (Stage 4E's exact requirement).
- `NumericalLevelCrossingObservation.__post_init__` gained exact-`bool`
  validation for `value_supplied`, `crossed_anywhere`, and
  `partial_boundary_crossing_observed`; `ObservedNumericalCrossingSummary`
  gained the same for `partial_boundary_observation_present`.
- The no-value path in `observe_numerical_level_crossing` previously
  returned immediately without attributing any bar — meaning an
  unrecognized boundary policy or out-of-window bar on the no-value
  path went completely undetected. It now attributes every bar first
  (via the same per-bar loop used on the value-supplied path),
  regardless of whether a value was supplied, and only then branches
  on `normalized_value is None`.

### 5. Validation order (final, as implemented)

For `observe_numerical_level_crossing`: (1) `level_kind`, (2) supplied
value, (3) `bundle` type, (4) observation context (which itself
validates policy/window/hash/market/type fields — an invalid policy is
therefore caught here, before any bar is ever touched), (5) every-bar
attribution (unconditionally, before the no-value/value-supplied
branch), (6) crossing calculation only when a value was supplied. No
`sorted()` call anywhere in this function — `bundle.bars`'s own
already-enforced ascending order is relied upon directly.

### 6. Total-exception contract

For every audited callable, invalid input in the test matrix now
raises only `NumericalCrossingContractError` or `SessionAttributionError`
— never a raw `TypeError`, `ValueError`, `OverflowError`, or
`AttributeError`. Confirmed both by direct reproduction scripts
(pre-correction) and by the new test suites (post-correction).

### 7. Sanitized-error contract

Error messages contain only a stable reason code, a stable field name,
and (where genuinely relevant and already-validated) `TARGET_VALUE`/
`STOP_VALUE` or an already-validated date. Canary-value tests
(`DO_NOT_RENDER_SECRET_CANARY`, `DO_NOT_RENDER_USER_CANARY`,
`DO_NOT_RENDER_SYMBOL_CANARY`) prove no invalid input's literal
contents are ever interpolated into an exception message, across
level-kind, numeric-value, evidence-ID, attribution, and context
validation paths.

### 8. Context-derived attribution contract

Unchanged from J4B.1: session attribution is derived solely from
`bundle.requested_window_start/end` and `bundle.entry_bar_policy/
exit_bar_policy`, now additionally guarded by exact-type checks on
those fields before any comparison.

### 9. Direct-construction limitations

`NumericalCrossingObservationContext`, `NumericalLevelCrossingObservation`,
and `ObservedNumericalCrossingSummary` each independently validate
their own declared invariants in `__post_init__`; a directly
constructed (forged) instance cannot carry a non-bool flag, an
unrecognized level kind/crossing type/pattern, an attribution
inconsistent with its bar and context, a mixed context identity, an
evidence ID inconsistent with trade/date/level-kind, or a safe/partial
group whose exact identity (session, bar, evidence ID, crossing type)
differs from an already-safe/already-partial first-observed group.

### 10. Factory-only earliest-crossing guarantee

Unchanged from J4B.1, restated for accuracy: a standalone
`NumericalLevelCrossingObservation` instance's `__post_init__` can only
prove *internal* self-consistency; it cannot by itself prove the
referenced bar is the chronologically earliest qualifying crossing
across the entire bundle. That guarantee is proven only by
`observe_numerical_level_crossing`'s own full-bundle scan and this
module's test suite — the docstring makes no stronger claim.

### 11. Requirement-to-test traceability (summary, not exhaustive 1:1)

The authorizing prompt specified 127 individually numbered scenarios
across Groups A–I. This phase's three test files (`test_price_path_
observed_crossing.py` — 56 pre-existing, unmodified; `test_price_path_
observed_crossing_invariants.py` — 85, 8 modified/added this phase;
`test_price_path_observed_crossing_final_contract.py` — 91, new this
phase) collectively exercise every requirement *group* and every
*concrete defect class* reproduced during the audit, with representative
and in several cases exhaustively-parametrized coverage (e.g. Group A's
value-contract rejections are covered by one parametrized test with 10
distinct IDs plus dedicated tests for huge-int/hostile-object/Decimal
cases). It does **not** instantiate 127 separately-named, 1:1-numbered
test functions. This is disclosed explicitly rather than claimed as
literal completion — see the honest coverage note at the top of
`test_price_path_observed_crossing_final_contract.py`.

### 12. Valid-output compatibility results

A dedicated `TestGroupICompatibilityBaseline` class captures and
re-verifies exact governed-field values (session attribution, crossing
type, first-observed/first-safe/first-partial sessions, any/safe
patterns) for India included-full entry/exit/partial/same-day, US
included-full entry/exit/partial/same-day, target-only, stop-only,
same-bar, target-before-stop, stop-before-target, partial-then-safe,
safe-then-partial, and no-values-supplied — all sixteen fixtures
produce identical output to the pre-J4B.2 implementation.

### 13. No persistence, no API/frontend, no version bump

Unchanged from J4B/J4B.1 — re-verified: `price_path_calculator.py`
still does not import `price_path_generation`/`price_path_claims`
(structural source check); neither of those modules nor
`paper_trading.py` reference any J4B/J4B.1/J4B.2 symbol; all six
version constants (`SOURCE_VERSION`, `EVIDENCE_BUNDLE_SCHEMA_VERSION`,
`SOURCE_MANIFEST_SCHEMA_VERSION`, `BOUNDARY_POLICY_VERSION`,
`CALCULATION_RULES_VERSION`, `PRICE_PATH_REPORT_SCHEMA_VERSION`) remain
pinned unchanged.

### 14. J4C not started; J4D not started; Stage J not complete

Unchanged from the sections above.

### 15. Known deviation: `test_price_path_observed_crossing.py`

That file is not in this phase's authorized-files list (Stage 10 named
only `test_price_path_observed_crossing_final_contract.py` as new, and
`test_price_path_observed_crossing_invariants.py` as modifiable-when-
necessary). The new `isinstance(bundle, PricePathEvidenceBundle)`/
`isinstance(bar, PricePathBar)` checks in `classify_bar_session_
attribution` mean a handful of that file's pre-existing tests, which
used duck-typed `_FakeBundle`/`_FakeBar` objects to reach the
policy-validation branch, now instead raise `SessionAttributionError`
at the new type-check gate — the tests still pass (any
`SessionAttributionError` satisfies their `pytest.raises` assertion),
but their docstrings' stated intent (proving policy validation
specifically) is no longer exactly what fires. This is disclosed here
rather than silently left inconsistent; equivalent, accurately-targeted
coverage of the policy-validation branch exists in `test_price_path_
observed_crossing_invariants.py` and `test_price_path_observed_
crossing_final_contract.py` via real forged `PricePathEvidenceBundle`
instances (`dataclasses.replace` on an already-valid bundle).

### Verification

Local: `pytest backend/tests -m "not postgres_integration"` →
**5036 passed, 0 failed, 187 deselected**, 124.15s. Log at
`/tmp/sprint3a-stage-j4b2-final-contract.log`. `git diff --check`
clean. Production diff: 132 insertions, 45 deletions in
`price_path_calculator.py` — a targeted correction, not a full-section
rewrite.

PostgreSQL: this phase adds unit tests only — no real-PG test was
added or modified; the real-PostgreSQL collection is expected to
remain at 187 (unchanged from the Stage J4B/J4B.1 baseline).

### Explicit non-claims

Stage J is **not** complete. This phase does not begin J4C, does not
begin J4D, does not change any report output, and does not change any
production behavior reachable through the live `/generate` endpoint.
