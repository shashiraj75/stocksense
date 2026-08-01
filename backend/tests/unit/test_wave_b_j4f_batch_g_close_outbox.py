"""
Wave B, Stage J4F — Batch G red tests: atomic close-to-current-outbox
behavior and version coexistence (Gate 3, IDs WB-J4F-01..06).

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


# WB-J4F-01 — close_paper_trade must accept an explicit parameter letting
# the caller request current (1.2.0) outbox-row insertion, rather than the
# helper reading an env var / feature flag internally (the prompt's
# explicit "no env-var reads inside the DB helper" instruction).
def test_wb_j4f_01_close_paper_trade_accepts_explicit_current_outbox_request():
    from services.postmortem import close_service
    sig = inspect.signature(close_service.close_paper_trade)
    assert "request_current_report_outbox" in sig.parameters or "insert_current_outbox" in sig.parameters, (
        f"WB-J4F-01: close_paper_trade signature {sig} has no explicit parameter for requesting current "
        "(1.2.0) outbox-row insertion (expected red pre-fix: only the legacy 1.0.0 outbox insert exists)."
    )


# WB-J4F-02 — the current-outbox insert helper must exist and be a genuine
# ON CONFLICT DO NOTHING idempotent insert, matching the pattern
# close_service._insert_outbox_record already uses for 1.0.0.
def test_wb_j4f_02_current_outbox_insert_helper_exists_and_is_idempotent():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "insert_current_outbox_record"), (
        "WB-J4F-02: current_report_generation.insert_current_outbox_record is not yet defined (expected red pre-fix)."
    )
    source = inspect.getsource(module.insert_current_outbox_record)
    assert "ON CONFLICT" in source and "DO NOTHING" in source, (
        "WB-J4F-02: insert_current_outbox_record does not visibly use ON CONFLICT ... DO NOTHING — "
        "risk of a duplicate outbox row on a retried/duplicate close."
    )


# WB-J4F-03 — the current-outbox insert must happen INSIDE the same
# transaction close_paper_trade already opens (atomic close-to-outbox),
# never as a second, separately-committed call after close_paper_trade
# returns.
def test_wb_j4f_03_current_outbox_insert_happens_inside_close_transaction():
    from services.postmortem import close_service
    source = inspect.getsource(close_service.close_paper_trade)
    assert "insert_current_outbox_record" in source or "current_report_generation" in source, (
        "WB-J4F-03: close_paper_trade does not visibly call the current (1.2.0) outbox insert helper "
        "from within its own `with conn.transaction():` block (expected red pre-fix: no such call exists yet)."
    )


# WB-J4F-04 — the current outbox row's requested_report_schema_version /
# requested_calculation_version / requested_rules_version must be passed
# in EXPLICITLY by the caller (paper_trading.py), never hardcoded inside
# close_service.py or read from an env var inside the DB helper.
def test_wb_j4f_04_current_outbox_identity_is_caller_supplied_not_env_read():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "insert_current_outbox_record"), (
        "WB-J4F-04: current_report_generation.insert_current_outbox_record is not yet defined (expected red pre-fix)."
    )
    sig = inspect.signature(module.insert_current_outbox_record)
    for required_param in ("schema_version", "calculation_version", "rules_version"):
        assert required_param in sig.parameters, (
            f"WB-J4F-04: insert_current_outbox_record signature {sig} is missing explicit parameter "
            f"{required_param!r} — identity must be caller-supplied, never read from os.environ inside the helper."
        )
    source = inspect.getsource(module.insert_current_outbox_record)
    assert "os.environ" not in source and "os.getenv" not in source, (
        "WB-J4F-04: insert_current_outbox_record reads an environment variable directly — "
        "identity must be passed in explicitly by the caller."
    )


# WB-J4F-05 — a duplicate close attempt for an already-CLOSED trade must
# never create a second current-outbox row (idempotent close contract,
# already true for the 1.0.0 outbox via _insert_outbox_record's own
# ON CONFLICT DO NOTHING + the WHERE status='OPEN' guard in close_paper_trade).
def test_wb_j4f_05_duplicate_close_never_double_inserts_current_outbox():
    from services.postmortem import close_service
    source = inspect.getsource(close_service.close_paper_trade)
    assert "status = 'OPEN'" in source, (
        "WB-J4F-05: close_paper_trade's UPDATE no longer guards WHERE status='OPEN' — "
        "a duplicate close race could re-run outbox insertion for an already-closed trade."
    )
    assert "insert_current_outbox_record" in source or "current_report_generation" in source, (
        "WB-J4F-05: close_paper_trade does not yet call the current outbox insert helper at all "
        "(expected red pre-fix) — cannot verify duplicate-close safety for the 1.2.0 identity."
    )


# WB-J4F-06 — a trade closed BEFORE the current-report feature is wired
# (i.e. only a 1.0.0/1.1.0 outbox row exists) must remain generatable via
# the explicit POST recovery endpoint later — the current-outbox insert
# must not be the ONLY way a 1.2.0 row can ever be created for that trade.
def test_wb_j4f_06_current_outbox_creatable_outside_close_time_for_backfill():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "insert_current_outbox_record"), (
        "WB-J4F-06: current_report_generation.insert_current_outbox_record is not yet defined (expected red pre-fix)."
    )
    sig = inspect.signature(module.insert_current_outbox_record)
    assert "conn" in sig.parameters, (
        f"WB-J4F-06: insert_current_outbox_record signature {sig} does not accept a caller-supplied conn — "
        "it must be callable standalone (e.g. from the POST recovery endpoint), not only from inside close_paper_trade's transaction."
    )
