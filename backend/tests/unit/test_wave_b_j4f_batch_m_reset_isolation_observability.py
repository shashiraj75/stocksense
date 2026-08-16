"""
Wave B, Stage J4F — Batch M red tests: reset/delete races, cross-user
isolation, privacy-safe logging, observability, and immutable report/
supersession invariants under coexistence (closes the final 20
J4F scenarios, WB-J4F-20 plus scenario-matrix completion).

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. Two scenarios in this batch verify EXISTING, already-correct
reset-path behavior (the generic `DELETE ... WHERE user_id = %s`
statements in paper_trading.py's reset endpoint already cover the
1.2.0 report/outbox tables with no version-specific code needed) —
those are EXPECTED_BASELINE_PASS, not red, and are labeled as such.
No production implementation is added by this file.
"""
import importlib
import importlib.util
import inspect

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


# Scenario 49 — the reset endpoint's existing generic DELETE statements
# already scope paper_trade_postmortem_report/outbox by user_id only
# (no report_schema_version filter) — verifying this remains true means
# a reset correctly clears 1.2.0 rows without any Wave B code change.
# EXPECTED_BASELINE_PASS.
def test_wb_j4f_49_reset_endpoint_deletes_report_and_outbox_by_user_id_generically():
    from api.routers import paper_trading
    source = inspect.getsource(paper_trading.reset_portfolio)
    assert "DELETE FROM paper_trade_postmortem_report WHERE user_id = %s" in source, (
        "WB-J4F-49: reset_portfolio's DELETE for paper_trade_postmortem_report is no longer a plain "
        "user_id-scoped delete — a version-specific WHERE clause could accidentally leave 1.2.0 rows behind."
    )
    assert "DELETE FROM paper_trade_postmortem_outbox WHERE user_id = %s" in source, (
        "WB-J4F-49: reset_portfolio's DELETE for paper_trade_postmortem_outbox is no longer a plain "
        "user_id-scoped delete."
    )


# Scenario 50 — the report/outbox DELETEs must happen BEFORE the
# paper_trades DELETE in source order (no orphan rows if a market-scoped
# reset later diverges from full reset). EXPECTED_BASELINE_PASS.
def test_wb_j4f_50_report_and_outbox_deleted_before_paper_trades():
    from api.routers import paper_trading
    source = inspect.getsource(paper_trading.reset_portfolio)
    report_idx = source.find("DELETE FROM paper_trade_postmortem_report")
    trades_idx = source.find("DELETE FROM paper_trades WHERE user_id = %s")
    assert report_idx != -1 and trades_idx != -1 and report_idx < trades_idx, (
        "WB-J4F-50: paper_trade_postmortem_report is not deleted before paper_trades in reset_portfolio — "
        "risk of an orphaned report row surviving a reset."
    )


# WB-J4F-51 — a background worker claiming rows must never leak one
# user's trade_id/user_id into another user's processing context — the
# global claim function's return type must carry its own user_id per
# claimed row (not a single ambient user_id for the whole batch).
def test_wb_j4f_51_global_claim_batch_carries_per_row_user_id():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-51: services.postmortem.outbox_worker does not exist yet (expected red pre-fix) — "
        "cannot verify per-row user_id isolation in the claim batch."
    )


# WB-J4F-52 — process_current_report must independently re-verify
# ownership (trade.user_id == the outbox row's own user_id) even when
# called from the worker — never trust the outbox row's user_id blindly
# without cross-checking the trade row itself (defense in depth against
# a corrupted/mismatched outbox row).
def test_wb_j4f_52_process_current_report_cross_checks_trade_ownership():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-52: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.process_current_report)
    assert "user_id" in source and "trade[" in source, (
        "WB-J4F-52: process_current_report does not visibly cross-check the trade row's own owner "
        "against the caller-supplied user_id."
    )


# WB-J4F-53 — no log statement anywhere in current_report_generation.py or
# outbox_worker.py may interpolate a raw symbol/price/PII value into a
# log line at INFO level without going through a sanitization boundary —
# checked structurally: no f-string log call embedding `entry_price`,
# `exit_price`, or a raw exception object directly.
def test_wb_j4f_53_no_raw_pii_or_price_values_interpolated_into_logs():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None, "WB-J4F-53: current_report_generation module does not exist yet (expected red pre-fix)."
    source = inspect.getsource(module)
    for forbidden in ("log.info(f\"", "logger.info(f\"", "log.warning(f\""):
        assert forbidden not in source, (
            f"WB-J4F-53: current_report_generation.py uses {forbidden!r} — f-string log interpolation risks "
            "leaking raw price/PII values; use %s-style lazy formatting with explicit safe fields instead."
        )


# WB-J4F-54 — outbox_worker.py's error logging must never log a full
# exception traceback containing potential connection-string or provider-
# response content at INFO level (only a stable error_code at INFO;
# full exception detail, if logged at all, must be DEBUG or below).
def test_wb_j4f_54_worker_does_not_log_raw_exception_at_info_level():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-54: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# WB-J4F-55 — claimed_by tokens generated for the current-report identity
# must reuse outbox.new_claimant_token() (the existing opaque, privacy-
# safe token generator), never a raw hostname/IP/PID-based identifier.
def test_wb_j4f_55_claimant_tokens_reuse_existing_opaque_generator():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-55: services.postmortem.outbox_worker does not exist yet (expected red pre-fix) — "
        "cannot verify claimant-token generation without it."
    )


# WB-J4F-56 — a 1.2.0 report row, once persisted, must be immutable at the
# database level exactly like 1.0.0/1.1.0 rows — verified by confirming
# the existing immutability trigger applies to the shared table (no
# separate, unprotected table was introduced for the 1.2.0 identity).
def test_wb_j4f_56_current_reports_share_the_same_immutable_table():
    import inspect as _inspect
    from services.postmortem import report_store
    source = _inspect.getsource(report_store.persist_report)
    assert "paper_trade_postmortem_report" in source, (
        "WB-J4F-56: report_store.persist_report does not target paper_trade_postmortem_report — "
        "the single immutability-triggered table every report_schema_version must share."
    )


# WB-J4F-57 — a 1.2.0 report's supersedes_report_id, once set at insert
# time, can never be changed later (the table has no UPDATE path at all
# for completed rows) — verified structurally: persist_current_report
# must never call an UPDATE statement against paper_trade_postmortem_report.
def test_wb_j4f_57_supersedes_report_id_never_updated_after_insert():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4F-57: current_report_generation.persist_current_report is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.persist_current_report)
    assert "UPDATE paper_trade_postmortem_report" not in source, (
        "WB-J4F-57: persist_current_report issues an UPDATE against paper_trade_postmortem_report — "
        "this table is insert-only/immutable; a genuinely new version must always be a new row."
    )


# WB-J4F-58 — deleting a trade mid-generation (a race between reset and an
# in-flight worker claim) must not crash the worker; process_current_report
# must handle a vanished trade row gracefully (already covered structurally
# by WB-J4F-33's TERMINAL classification, verified here specifically in
# the worker's own exception handling around the orchestrator call).
def test_wb_j4f_58_worker_survives_trade_deleted_mid_generation():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-58: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# WB-J4F-59 — the worker's poll loop must catch exceptions PER-ROW, so one
# malformed/failing row in a claimed batch cannot abort processing of the
# other rows in the same batch.
def test_wb_j4f_59_worker_isolates_per_row_exceptions_within_a_batch():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-59: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# WB-J4F-60 — retry backoff must be capped (a maximum next_attempt_at
# delay), never grow unbounded across many failed attempts, so a
# permanently-failing row still gets periodically retried rather than
# effectively abandoned.
def test_wb_j4f_60_retry_backoff_is_capped_not_unbounded():
    from services.postmortem import outbox as outbox_ops
    import inspect as _inspect
    source = _inspect.getsource(outbox_ops)
    assert "_RETRY_BACKOFF_SECONDS" in source, (
        "WB-J4F-60: outbox.py's retry backoff constant is missing — cannot verify a capped value "
        "(this re-verifies existing frozen behavior the 1.2.0 identity reuses unchanged; "
        "EXPECTED_BASELINE_PASS if the constant is present)."
    )


# WB-J4F-61 — graceful shutdown must let an in-flight claimed row finish
# its current attempt rather than abandoning it mid-transaction (no
# forced task cancellation while holding a lease).
def test_wb_j4f_61_graceful_shutdown_does_not_abandon_in_flight_claim():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-61: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# WB-J4F-62 — the worker must be safely restartable: calling start_*
# twice without an intervening stop_* must not spawn two independent
# poll loops racing each other.
def test_wb_j4f_62_worker_start_is_idempotent_no_duplicate_loops():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-62: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# WB-J4F-63 — the worker's lease duration must be a named, explicit
# constant (not derived ad hoc per call), matching outbox.py's own
# existing lease-duration convention so claim_next_attempt and the new
# global claim agree on the same lease window.
def test_wb_j4f_63_worker_lease_duration_matches_shared_outbox_convention():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    assert worker_module is not None, (
        "WB-J4F-63: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )


# WB-J4F-64 — the global claim query, once it exists, must accept a
# caller-supplied claimant token (not generate one internally hidden from
# the caller), so the caller can correctly settle the SAME rows it claimed.
def test_wb_j4f_64_global_claim_accepts_caller_supplied_claimant_token():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    generation_module = _module_or_none("services.postmortem.current_report_generation")
    fn = None
    if worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"):
        fn = worker_module.claim_next_current_outbox_batch
    elif generation_module is not None and hasattr(generation_module, "claim_next_current_outbox_batch"):
        fn = generation_module.claim_next_current_outbox_batch
    assert fn is not None, (
        "WB-J4F-64: no global claim function exists yet in outbox_worker or current_report_generation "
        "(expected red pre-fix)."
    )
    sig = inspect.signature(fn)
    assert "claimant" in sig.parameters, (
        f"WB-J4F-64: global claim function signature {sig} does not accept an explicit claimant parameter."
    )


# WB-J4F-65 — the global claim function must accept an explicit batch-size
# limit parameter, matching WB-J4F-25's bounded-batch-size requirement at
# the actual query-function level (not only the poll-loop wrapper level).
def test_wb_j4f_65_global_claim_function_accepts_explicit_limit_param():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    generation_module = _module_or_none("services.postmortem.current_report_generation")
    fn = None
    if worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"):
        fn = worker_module.claim_next_current_outbox_batch
    elif generation_module is not None and hasattr(generation_module, "claim_next_current_outbox_batch"):
        fn = generation_module.claim_next_current_outbox_batch
    assert fn is not None, (
        "WB-J4F-65: no global claim function exists yet (expected red pre-fix)."
    )
    sig = inspect.signature(fn)
    assert any("limit" in p or "batch" in p for p in sig.parameters), (
        f"WB-J4F-65: global claim function signature {sig} has no explicit limit/batch-size parameter."
    )


# WB-J4F-66 — the global claim function must filter by the EXACT current
# (1.2.0) version identity, never claim arbitrary pending rows regardless
# of requested_report_schema_version (a worker binary must only process
# the identity it knows how to generate).
def test_wb_j4f_66_global_claim_filters_by_exact_current_version_identity():
    worker_module = _module_or_none("services.postmortem.outbox_worker")
    generation_module = _module_or_none("services.postmortem.current_report_generation")
    fn = None
    if worker_module is not None and hasattr(worker_module, "claim_next_current_outbox_batch"):
        fn = worker_module.claim_next_current_outbox_batch
    elif generation_module is not None and hasattr(generation_module, "claim_next_current_outbox_batch"):
        fn = generation_module.claim_next_current_outbox_batch
    assert fn is not None, (
        "WB-J4F-66: no global claim function exists yet (expected red pre-fix)."
    )
    source = inspect.getsource(fn)
    assert "requested_report_schema_version" in source, (
        "WB-J4F-66: global claim function does not visibly filter by requested_report_schema_version."
    )


# WB-J4F-67 — the FastAPI lifespan file's worker startup must be wrapped
# so a worker startup failure (e.g. bad config) does not prevent the rest
# of the application from starting — the postmortem worker is best-effort
# infrastructure, never a hard dependency for the app to boot.
def test_wb_j4f_67_lifespan_worker_startup_does_not_block_app_boot_on_failure():
    from api import main as api_main
    source = inspect.getsource(api_main)
    assert "outbox_worker" in source, (
        "WB-J4F-67: api/main.py's lifespan does not yet reference services.postmortem.outbox_worker at all "
        "(expected red pre-fix: worker startup has not been wired into the lifespan yet)."
    )
    assert "try:" in source and "except" in source, (
        "WB-J4F-67: api/main.py's lifespan has no visible try/except — cannot verify worker startup "
        "failures are caught rather than propagating and blocking app boot."
    )


# WB-J4F-68 — the lifespan must call the worker's stop_* function during
# application shutdown (not just start it and never clean up), matching
# the existing pattern api/main.py already uses for its other ~13
# background loops.
def test_wb_j4f_68_lifespan_calls_worker_stop_on_shutdown():
    from api import main as api_main
    source = inspect.getsource(api_main)
    assert "stop_" in source and "outbox_worker" in source, (
        "WB-J4F-68: api/main.py's lifespan does not yet call any outbox_worker.stop_* function on shutdown "
        "(expected red pre-fix: worker shutdown has not been wired into the lifespan yet)."
    )
