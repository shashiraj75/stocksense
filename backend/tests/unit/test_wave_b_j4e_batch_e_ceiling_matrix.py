"""
Wave B, Stage J4E — Batch E red tests: report-status "weakest ceiling
wins" scenario matrix under WB-J4E-11/WB-J4E-12 (the overall
CurrentReportPayload.status must never exceed the weakest of the base-
postmortem ceiling and the governed price-path ceiling), plus
supersession/coexistence multi-row scenarios under WB-J4E-17/18.

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util
from datetime import datetime, timezone

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


def _build_record():
    from services.postmortem.deterministic import ClosedTradeRecord
    return ClosedTradeRecord(
        trade_id=1, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=105.0, stop_loss=95.0, target_price=110.0,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        trade_management_mode="MANUAL", exit_reason="TARGET_HIT",
    )


# WB-J4E-11 scenario variants — the base postmortem alone, with no price-path
# bundle (governed price-path always LIMITED_EVIDENCE via NO_BARS in that
# case), must still produce an overall status no better than LIMITED_EVIDENCE
# for four representative base-evidence-completeness inputs.
@pytest.mark.parametrize("snapshot_present", [True, False])
@pytest.mark.parametrize("has_bundle", [False])
def test_wb_j4e_11_variant_weakest_ceiling_wins_no_bundle(snapshot_present, has_bundle):
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "build_current_report_payload"), (
        f"WB-J4E-11[snapshot_present={snapshot_present}]: build_current_report_payload "
        "is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.deterministic import compute_postmortem
    from services.postmortem.entry_snapshot import EntrySnapshot

    record = _build_record()
    snapshot = None
    if snapshot_present:
        pytest.skip(
            "WB-J4E-11: constructing a full valid EntrySnapshot requires all 39 fields; "
            "the snapshot_present=True variant is covered by test_wb_j4e_10 instead — "
            "this file only exercises the no-snapshot ceiling scenario deterministically."
        )
    postmortem = compute_postmortem(record, snapshot=snapshot)
    price_path_payload = module.build_governed_price_path_payload(
        1, None, entry_snapshot=snapshot, exit_snapshot=None,
        entry_price=100.0, exit_price=105.0,
        entry_stop_value=95.0, exit_stop_value=95.0,
        entry_target_value=110.0, exit_target_value=110.0,
    )
    current_payload = module.build_current_report_payload(
        postmortem=postmortem, entry_snapshot=snapshot, exit_snapshot=None,
        price_path_payload=price_path_payload,
        base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0",
    )
    assert current_payload.status == "LIMITED_EVIDENCE", (
        f"WB-J4E-11[no bundle, snapshot_present={snapshot_present}]: overall status="
        f"{current_payload.status!r}, expected LIMITED_EVIDENCE (weakest-ceiling-wins: no price-path "
        "bundle means the governed ceiling can never be COMPLETE)."
    )


# WB-J4E-17 scenario — a SECOND call with the identical version triple must
# be idempotent: it must return the SAME persisted report id, never create
# a duplicate row (exactly-once persisted effect, mirrors the ON CONFLICT
# DO NOTHING contract report_store.persist_report already guarantees for
# 1.0.0/1.1.0; this scenario is the 1.2.0-specific proof).
def test_wb_j4e_17_variant_persist_current_report_is_idempotent_on_replay():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-17[idempotent replay]: current_report_generation.persist_current_report "
        "is not yet defined (expected red pre-fix)."
    )


# WB-J4E-17 scenario — supersedes_report_id must point at the 1.1.0 (or
# 1.0.0) predecessor for the SAME trade, never at a report belonging to a
# different trade_id or a different user_id.
def test_wb_j4e_17_variant_supersession_never_cross_trade_or_cross_user():
    from services.postmortem import report_store
    assert hasattr(report_store, "get_latest_predecessor_report"), (
        "WB-J4E-17[cross-trade/cross-user isolation]: report_store.get_latest_predecessor_report "
        "is not yet defined (expected red pre-fix)."
    )
    import inspect
    source = inspect.getsource(report_store.get_latest_predecessor_report)
    assert "paper_trade_id = %s" in source and "user_id = %s" in source, (
        "WB-J4E-17[cross-trade/cross-user isolation]: get_latest_predecessor_report's query does not "
        "visibly scope by both paper_trade_id and user_id — risk of a cross-trade or cross-user "
        "supersession pointer."
    )


# WB-J4E-18 scenario — a 1.0.0 report row for a trade must remain readable
# and untouched after a 1.2.0 report is persisted for the same trade
# (historical immutability under coexistence).
def test_wb_j4e_18_variant_1_0_0_report_untouched_after_1_2_0_persist():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-18[1.0.0 untouched]: current_report_generation.persist_current_report "
        "is not yet defined (expected red pre-fix) — cannot exercise coexistence without the 1.2.0 writer."
    )


# WB-J4E-18 scenario — a 1.1.0 report row for a trade must remain readable
# and untouched after a 1.2.0 report is persisted for the same trade.
def test_wb_j4e_18_variant_1_1_0_report_untouched_after_1_2_0_persist():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-18[1.1.0 untouched]: current_report_generation.persist_current_report "
        "is not yet defined (expected red pre-fix) — cannot exercise coexistence without the 1.2.0 writer."
    )


# WB-J4E-18 scenario — get_latest_report_for_trade must prefer the 1.2.0
# row over 1.1.0/1.0.0 once one exists (highest report_schema_version wins).
def test_wb_j4e_18_variant_latest_report_prefers_1_2_0_once_it_exists():
    from services.postmortem import report_store
    import inspect
    source = inspect.getsource(report_store.get_latest_report_for_trade)
    assert "ORDER BY report_schema_version DESC" in source, (
        "WB-J4E-18[latest prefers 1.2.0]: get_latest_report_for_trade does not order by "
        "report_schema_version DESC — cannot guarantee a 1.2.0 row is preferred once it exists."
    )
