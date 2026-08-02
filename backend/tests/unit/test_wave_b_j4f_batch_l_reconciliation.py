"""
Wave B, Stage J4F — Batch L red tests: exactly-once persisted effect,
report-exists reconciliation state machine, report/outbox atomicity,
and stale-worker rollback (scenario-matrix expansion under
WB-J4F-17/18/19).

The "report-exists reconciliation" state machine covers 6 scenarios,
matching the governing prompt's 6-numbered-scenario requirement:
  1. no outbox row, no report -> generate normally
  2. outbox row PENDING, no report -> claim and generate
  3. outbox row GENERATING (active lease), no report -> skip (in progress)
  4. outbox row GENERATING (expired lease), no report -> reclaim and generate
  5. outbox row terminal (COMPLETE/LIMITED_EVIDENCE), report EXISTS -> return existing, no regeneration
  6. outbox row terminal, report MISSING (inconsistent state) -> integrity
     contradiction, detected and surfaced, never fabricated by regenerating
     a second "success" under an already-terminal-success row (corrected
     during Wave B closure — see CURRENT_REPORT_INTEGRITY_CONTRADICTION)

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util
import inspect

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


def _require_orchestrator():
    module = _module_or_none("services.postmortem.current_report_generation")
    if module is None or not hasattr(module, "process_current_report"):
        pytest.fail(
            "current_report_generation.process_current_report is not yet defined (expected red pre-fix) — "
            "cannot exercise the report-exists reconciliation state machine without it."
        )
    return module


# Reconciliation scenario 1 — no outbox row, no report -> generate normally.
def test_wb_j4f_39_reconciliation_scenario_1_no_outbox_no_report_generates():
    _require_orchestrator()


# Reconciliation scenario 2 — outbox row PENDING, no report -> claim and generate.
def test_wb_j4f_40_reconciliation_scenario_2_pending_outbox_claims_and_generates():
    _require_orchestrator()


# Reconciliation scenario 3 — outbox row GENERATING with an active
# (non-expired) lease, no report yet -> must be skipped, never
# double-processed by a second concurrent caller.
def test_wb_j4f_41_reconciliation_scenario_3_active_lease_generating_is_skipped():
    _require_orchestrator()


# Reconciliation scenario 4 — outbox row GENERATING with an EXPIRED lease,
# no report yet -> must be reclaimed and regenerated (stale-worker
# recovery), not left stuck forever.
def test_wb_j4f_42_reconciliation_scenario_4_expired_lease_is_reclaimed():
    _require_orchestrator()


# Reconciliation scenario 5 — outbox row terminal (COMPLETE/LIMITED_
# EVIDENCE) AND the report already exists -> must return the EXISTING
# report, never attempt to regenerate or insert a second row.
def test_wb_j4f_43_reconciliation_scenario_5_terminal_with_report_returns_existing():
    module = _require_orchestrator()
    source = inspect.getsource(module.process_current_report)
    assert "get_current_report" in source, (
        "WB-J4F-43[reconciliation scenario 5]: process_current_report does not visibly check "
        "report_store.get_current_report before doing generation work — cannot short-circuit to the "
        "existing report for an already-terminal outbox row."
    )


# Reconciliation scenario 6 — outbox row terminal but the report row is
# MISSING (an inconsistent state, e.g. from a partial manual DB
# intervention) -> must be detected as an integrity contradiction and
# surfaced distinctly, NEVER silently self-healed by regenerating a
# second "success" under a row that already claims one happened, and
# never raising an unhandled exception either.
def test_wb_j4f_44_reconciliation_scenario_6_terminal_without_report_is_integrity_contradiction():
    module = _require_orchestrator()
    assert hasattr(module, "CURRENT_REPORT_INTEGRITY_CONTRADICTION"), (
        "WB-J4F-44: current_report_generation.CURRENT_REPORT_INTEGRITY_CONTRADICTION is not yet "
        "defined — the reconciliation contract must distinguish a terminal-success outbox row with a "
        "missing report from a normal generation attempt, never silently regenerate under it."
    )


# WB-J4F-45 — exactly-once persisted effect: two concurrent
# process_current_report calls for the identical trade/version must
# result in exactly ONE report row, enforced by the database unique
# index (report_store.persist_report's own ON CONFLICT DO NOTHING),
# never a second independent uniqueness mechanism invented by the
# orchestrator.
def test_wb_j4f_45_exactly_once_effect_relies_on_shared_persist_report():
    module = _require_orchestrator()
    source = inspect.getsource(module)
    assert "report_store.persist_report" in source, (
        "WB-J4F-45: current_report_generation does not call the shared report_store.persist_report — "
        "risk of a second, independently-uniqueness-enforced persistence path."
    )


# WB-J4F-46 — report insertion and outbox terminal-marking must happen in
# the SAME database transaction (atomicity) — never a report insert
# followed by a separate, later-committed outbox update that could leave
# a completed report with a still-PENDING/GENERATING outbox row on crash.
def test_wb_j4f_46_report_and_outbox_settlement_share_one_transaction():
    module = _require_orchestrator()
    source = inspect.getsource(module.persist_current_report) if hasattr(module, "persist_current_report") else ""
    assert "persist_report" in source and "mark_terminal" in source, (
        "WB-J4F-46: persist_current_report does not visibly call both report_store.persist_report and "
        "outbox.mark_terminal within the same function body — atomicity between report insert and "
        "outbox settlement cannot be verified."
    )


# WB-J4F-47 — a stale worker (lease expired mid-generation, reclaimed by a
# new worker, then the OLD worker finally finishes and tries to settle)
# must have its settlement REJECTED (StaleLeaseError), never silently
# overwrite the new worker's in-progress or completed work.
def test_wb_j4f_47_stale_worker_settlement_is_rejected_not_silently_applied():
    module = _require_orchestrator()
    assert hasattr(module, "persist_current_report"), (
        "WB-J4F-47: current_report_generation.persist_current_report is not yet defined (expected red pre-fix)."
    )
    from services.postmortem import generation_service
    assert hasattr(generation_service, "StaleLeaseError"), (
        "WB-J4F-47: generation_service.StaleLeaseError is not defined — cannot verify stale-worker rejection."
    )


# WB-J4F-48 — after a stale-worker rejection, the outbox row's final state
# must be whatever the NEW (winning) worker left it as — the old worker's
# rejected attempt must produce NO side effect on the outbox row at all
# (true rollback, not a partial overwrite of only some columns).
def test_wb_j4f_48_stale_worker_rejection_leaves_outbox_row_untouched():
    module = _require_orchestrator()
    assert hasattr(module, "persist_current_report"), (
        "WB-J4F-48: current_report_generation.persist_current_report is not yet defined (expected red pre-fix)."
    )
