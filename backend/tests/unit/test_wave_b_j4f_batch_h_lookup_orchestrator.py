"""
Wave B, Stage J4F — Batch H red tests: exact current-version-scoped
outbox/report lookup and shared processing-orchestrator reuse
(Gate 3, IDs WB-J4F-07..12).

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


# WB-J4F-07 — an outbox lookup for the current identity must NEVER use
# "ORDER BY created_at DESC LIMIT 1" (which could return a stale/wrong-
# version row); it must filter explicitly by the exact version triple.
def test_wb_j4f_07_current_outbox_lookup_is_exact_version_scoped_not_latest():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "insert_current_outbox_record"), (
        "WB-J4F-07: current_report_generation.insert_current_outbox_record is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.insert_current_outbox_record)
    assert "ORDER BY created_at DESC LIMIT 1" not in source, (
        "WB-J4F-07: current-outbox lookup uses 'ORDER BY created_at DESC LIMIT 1' — this can return "
        "a row for the WRONG version identity; lookups must filter explicitly by the exact version triple."
    )
    assert "requested_report_schema_version = %s" in source and "requested_calculation_version = %s" in source, (
        "WB-J4F-07: current-outbox lookup does not visibly filter by the exact "
        "(requested_report_schema_version, requested_calculation_version, requested_rules_version) triple."
    )


# WB-J4F-08 — a claim/find-claimable lookup for the current outbox identity
# used by the background worker must also be exact-version-scoped, never a
# global "any pending row" scan that could claim a 1.0.0/1.1.0 row under
# 1.2.0-specific processing logic.
def test_wb_j4f_08_worker_claim_lookup_will_be_version_scoped():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, (
        "WB-J4F-08: services.postmortem.outbox_worker does not exist yet (expected red pre-fix: "
        "the background worker module has not been written)."
    )


# WB-J4F-09 — process_current_report (the shared orchestrator) must be the
# ONLY function any of the three call sites (close-time, POST recovery,
# background worker) invokes to generate a 1.2.0 report — no call site may
# duplicate phases 1-5 independently.
def test_wb_j4f_09_shared_orchestrator_function_exists():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4F-09: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )


# WB-J4F-10 — paper_trading.py's close-time best-effort path must call the
# shared orchestrator, never re-implement generation inline.
def test_wb_j4f_10_paper_trading_close_time_path_calls_shared_orchestrator():
    from api.routers import paper_trading
    source = inspect.getsource(paper_trading)
    assert "process_current_report" in source, (
        "WB-J4F-10: api/routers/paper_trading.py does not yet call "
        "current_report_generation.process_current_report anywhere (expected red pre-fix: close-time "
        "wiring for the 1.2.0 orchestrator has not been added)."
    )


# WB-J4F-11 — the explicit POST /postmortem/{trade_id}/generate endpoint
# must ALSO call the same shared orchestrator (not a second, endpoint-local
# copy of the generation logic).
def test_wb_j4f_11_post_generate_endpoint_calls_shared_orchestrator():
    from api.routers import paper_trading
    source = inspect.getsource(paper_trading)
    assert "def generate_postmortem" in source or "postmortem/{trade_id}/generate" in source or "postmortem_generate" in source, (
        "WB-J4F-11: could not locate the existing POST /postmortem/{trade_id}/generate endpoint in "
        "paper_trading.py — cannot verify shared-orchestrator reuse without first confirming the entry point exists."
    )
    occurrences = source.count("process_current_report")
    assert occurrences >= 2, (
        f"WB-J4F-11: current_report_generation.process_current_report is referenced {occurrences} time(s) in "
        "paper_trading.py; expected at least 2 (close-time best-effort AND the explicit POST recovery endpoint) "
        "once both call sites are wired — currently 0 (expected red pre-fix)."
    )


# WB-J4F-12 — the background worker (once it exists) must ALSO call the
# same shared orchestrator, never a fourth independent implementation.
def test_wb_j4f_12_background_worker_will_call_shared_orchestrator():
    module = _module_or_none("services.postmortem.outbox_worker")
    assert module is not None, (
        "WB-J4F-12: services.postmortem.outbox_worker does not exist yet (expected red pre-fix)."
    )
