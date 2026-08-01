"""
Wave B, Stage J4E — Batch B red tests: immutable snapshot consumption
and governed report payload construction (Gate 1, IDs WB-J4E-07..12).

Written and run against genuine PRE-FIX production code — every test
here is expected to FAIL until the frozen-contract implementation
lands. Absent module/symbols are probed via importlib.util.find_spec /
getattr so collection always succeeds; the assertion itself carries the
intended failure. No production implementation is added by this file.
"""
import importlib
import importlib.util

import pytest


def _spec(dotted_name: str):
    return importlib.util.find_spec(dotted_name)


def _module_or_none(dotted_name: str):
    if _spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


# WB-J4E-07 — a pure payload-building function must exist that takes an
# entry/exit snapshot pair, never a mutable paper_trades row, as its
# level-history/basis input.
def test_wb_j4e_07_build_governed_price_path_payload_exists():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "build_governed_price_path_payload"), (
        "WB-J4E-07: services.postmortem.current_report_generation.build_governed_price_path_payload "
        "is not yet defined (expected red pre-fix: no governed payload builder exists)."
    )


# WB-J4E-08 — the no-bundle (no price-path evidence acquired) branch must
# produce an honest NO_BARS conclusion, never fabricate a positive touch.
def test_wb_j4e_08_no_bundle_produces_no_bars_not_fabricated_touch():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "build_governed_price_path_payload"), (
        "WB-J4E-08: cannot exercise the no-bundle fallback path — "
        "build_governed_price_path_payload is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.entry_snapshot import EntrySnapshot  # noqa: F401 (existence sanity only)

    payload = module.build_governed_price_path_payload(
        1, None, entry_snapshot=None, exit_snapshot=None,
        entry_price=100.0, exit_price=105.0,
        entry_stop_value=95.0, exit_stop_value=95.0,
        entry_target_value=110.0, exit_target_value=110.0,
    )
    target_status = payload.target_evidence.touch.status
    stop_status = payload.stop_evidence.touch.status
    assert target_status == "NO_BARS" and stop_status == "NO_BARS", (
        f"WB-J4E-08: no-bundle path produced target={target_status!r} stop={stop_status!r}, "
        "expected honest NO_BARS for both levels rather than any fabricated positive touch."
    )


# WB-J4E-09 — a snapshot's OWN governed fields must be read, never the
# mutable current paper_trades columns as a substitute.
def test_wb_j4e_09_payload_reads_snapshot_governed_fields_not_mutable_row():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None, (
        "WB-J4E-09: current_report_generation module does not exist yet (expected red pre-fix); "
        "cannot verify snapshot-field consumption source."
    )
    import inspect
    source = inspect.getsource(module)
    assert "entry_snapshot.level_history_contract_version" in source or "entry_snapshot.initial_" in source, (
        "WB-J4E-09: build_governed_price_path_payload does not visibly consume the immutable "
        "entry_snapshot's own governed fields (level_history_contract_version / initial_*_modified_after_entry) — "
        "risk of silently substituting mutable paper_trades columns instead (expected red pre-fix)."
    )


# WB-J4E-10 — a legacy row with entry_snapshot=None must never be reported as
# governed FALSE (a definitive negative) — it must fall back to INSUFFICIENT_EVIDENCE.
def test_wb_j4e_10_legacy_no_snapshot_never_fabricates_governed_false():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "build_current_report_payload"), (
        "WB-J4E-10: build_current_report_payload is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
    from datetime import datetime, timezone

    record = ClosedTradeRecord(
        trade_id=1, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=105.0, stop_loss=95.0, target_price=110.0,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        trade_management_mode="MANUAL", exit_reason="TARGET_HIT",
    )
    postmortem = compute_postmortem(record, snapshot=None)
    price_path_payload = module.build_governed_price_path_payload(
        1, None, entry_snapshot=None, exit_snapshot=None,
        entry_price=100.0, exit_price=105.0,
        entry_stop_value=95.0, exit_stop_value=95.0,
        entry_target_value=110.0, exit_target_value=110.0,
    )
    current_payload = module.build_current_report_payload(
        postmortem=postmortem, entry_snapshot=None, exit_snapshot=None,
        price_path_payload=price_path_payload,
        base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0",
    )
    assert current_payload.status != "COMPLETE", (
        f"WB-J4E-10: a legacy trade with no entry snapshot produced status="
        f"{current_payload.status!r}; a legacy/unknown level-history trade must never report COMPLETE."
    )


# WB-J4E-11 — the structured report must contain all 4 required
# sub-sections: raw evidence, level-history evidence, governed
# conclusions, version-and-provenance.
def test_wb_j4e_11_structured_report_has_four_required_subsections():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "CurrentReportPayload"), (
        "WB-J4E-11: current_report_generation.CurrentReportPayload is not yet defined (expected red pre-fix)."
    )
    payload_cls = module.CurrentReportPayload
    field_names = {f.name for f in __import__("dataclasses").fields(payload_cls)}
    required = {"raw_evidence", "level_history_evidence", "governed_conclusions", "version_and_provenance"}
    missing = required - field_names
    assert not missing, (
        f"WB-J4E-11: CurrentReportPayload is missing required structured-report sub-sections {missing} "
        "(expected red pre-fix: the current draft uses different field names)."
    )


# WB-J4E-12 — version_and_provenance must include the exact report_schema_version,
# calculation_version and claim_rules_version actually used to build the report.
def test_wb_j4e_12_version_and_provenance_carries_exact_triple():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "build_current_report_payload"), (
        "WB-J4E-12: build_current_report_payload is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
    from datetime import datetime, timezone

    record = ClosedTradeRecord(
        trade_id=1, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=105.0, stop_loss=95.0, target_price=110.0,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        trade_management_mode="MANUAL", exit_reason="TARGET_HIT",
    )
    postmortem = compute_postmortem(record, snapshot=None)
    price_path_payload = module.build_governed_price_path_payload(
        1, None, entry_snapshot=None, exit_snapshot=None,
        entry_price=100.0, exit_price=105.0,
        entry_stop_value=95.0, exit_stop_value=95.0,
        entry_target_value=110.0, exit_target_value=110.0,
    )
    current_payload = module.build_current_report_payload(
        postmortem=postmortem, entry_snapshot=None, exit_snapshot=None,
        price_path_payload=price_path_payload,
        base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0",
    )
    prov = current_payload.version_and_provenance
    assert prov.get("report_schema_version") == "1.2.0", (
        f"WB-J4E-12: version_and_provenance.report_schema_version == {prov.get('report_schema_version')!r}, "
        "expected exactly '1.2.0'."
    )
    assert prov.get("calculation_version") == "1.1.0+price_path:1.0.0+governed:2.0.0+src:1.1.0", (
        f"WB-J4E-12: version_and_provenance.calculation_version == {prov.get('calculation_version')!r}, "
        "expected the exact contract-formatted string."
    )
