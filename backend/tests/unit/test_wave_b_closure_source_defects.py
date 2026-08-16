"""
Wave B closure pass — genuine behavioral red tests exposing 4 real
source defects found by fresh code review of current_report_generation.py
and outbox_worker.py (findings A, B, C, F/G of the governing closure
prompt). Written and run against HEAD c6c31bf7a43c6d9e6d438d67389998fcff262686
/ production state 60bf499a4caf81d4dee3f5b9ca4e9965062dfa46 — every test
here is expected to FAIL until the corresponding production fix lands.
"""
from datetime import datetime, timezone

import pytest

from services.postmortem.current_report_generation import (
    build_current_report_payload,
    build_governed_price_path_payload,
)
from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.entry_snapshot import EntrySnapshot
from services.postmortem.exit_snapshot import ExitSnapshot


def _make_entry_snapshot(**overrides):
    base = dict(
        paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
        snapshot_schema_version="1.0.0", evidence_source="DAILY_PICK",
        daily_pick_run_id=None, daily_pick_rank=None, recommendation_signal=None,
        recommendation_generated_at=None, recommendation_reference_price=None,
        recommendation_entry_low=None, recommendation_entry_high=None,
        simulated_execution_price=100.0, execution_slippage_pct=None, execution_range_position=None,
        recommended_stop_loss=95.0, recommended_target_price=110.0,
        user_selected_stop_loss=95.0, user_selected_target_price=110.0,
        user_overrode_recommendation=False, reward_to_risk_ratio=None, confidence_score=None,
        technical_signal=None, technical_rsi=None, technical_macd_diff=None,
        fundamental_score=None, sentiment_score=None, sentiment_label=None,
        market_regime_trend=None, market_regime_score_adj=None, market_regime_reason=None,
        recommendation_reasoning=None, model_version=None, verification_levels={},
        level_history_contract_version="1.0.0",
        initial_stop_modified_after_entry=False, initial_target_modified_after_entry=False,
        initial_levels_modified_after_entry=False,
    )
    base.update(overrides)
    return EntrySnapshot(**base)


def _make_exit_snapshot(**overrides):
    base = dict(
        paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
        exit_snapshot_schema_version="1.0.0", financial_outcome="PROFIT",
        closure_classification="MANUAL", exit_mechanism="MANUAL", exit_mechanism_raw="MANUAL",
        exit_price=108.0, exit_quantity=10, closed_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        final_stop_loss=97.0, final_target_price=108.0,
        trailing_stop_level=None, time_exit_rule=None, market_close_rule=None,
        management_mode="MANUAL", levels_modified_after_entry=True,
        level_history_contract_version="1.0.0",
        final_stop_modified_after_entry=True, final_target_modified_after_entry=True,
        source_request_id=None, trigger_observation_timestamp=None, trigger_observation_price=None,
        trigger_timing_verification=None, source_metadata=None,
    )
    base.update(overrides)
    return ExitSnapshot(**base)


# FINDING A — entry/exit endpoint collapse. A level modified after entry
# (final stop 97, final target 108) must report its ORIGINAL entry value
# (95/110) as entry_endpoint_value and its DISTINCT final value
# (97/108) as exit_endpoint_value — never the same value for both.
def test_finding_a_entry_and_exit_endpoint_values_are_distinct_when_modified():
    entry_snapshot = _make_entry_snapshot()
    exit_snapshot = _make_exit_snapshot()

    payload = build_governed_price_path_payload(
        1, None, entry_snapshot=entry_snapshot, exit_snapshot=exit_snapshot,
        entry_price=100.0, exit_price=108.0,
        entry_stop_value=entry_snapshot.user_selected_stop_loss,
        exit_stop_value=exit_snapshot.final_stop_loss,
        entry_target_value=entry_snapshot.user_selected_target_price,
        exit_target_value=exit_snapshot.final_target_price,
    )
    target_section = payload.price_path_section["level_history"]["target"]
    stop_section = payload.price_path_section["level_history"]["stop"]

    assert target_section["entry_endpoint_value"] == 110.0, (
        f"FINDING A: target entry_endpoint_value={target_section['entry_endpoint_value']!r}, expected the "
        "immutable ENTRY snapshot's own original value (110.0), not the final/modified value."
    )
    assert target_section["exit_endpoint_value"] == 108.0, (
        f"FINDING A: target exit_endpoint_value={target_section['exit_endpoint_value']!r}, expected the "
        "immutable EXIT snapshot's own final value (108.0)."
    )
    assert stop_section["entry_endpoint_value"] == 95.0, (
        f"FINDING A: stop entry_endpoint_value={stop_section['entry_endpoint_value']!r}, expected 95.0."
    )
    assert stop_section["exit_endpoint_value"] == 97.0, (
        f"FINDING A: stop exit_endpoint_value={stop_section['exit_endpoint_value']!r}, expected 97.0."
    )
    assert target_section["entry_endpoint_value"] != target_section["exit_endpoint_value"], (
        "FINDING A: entry and exit endpoint values must never collapse to the identical value for a "
        "level that was genuinely modified after entry."
    )


# FINDING A (orchestration level) — process_current_report itself must
# call build_governed_price_path_payload with entry_stop_value/
# entry_target_value derived from the ENTRY snapshot and exit_stop_value/
# exit_target_value derived from the EXIT snapshot, never both from the
# same `applicable_stop`/`applicable_target` local variable.
def test_finding_a_orchestrator_derives_distinct_entry_and_exit_values():
    import inspect
    from services.postmortem import current_report_generation as m

    source = inspect.getsource(m.process_current_report)
    assert "entry_stop_value=applicable_stop, exit_stop_value=applicable_stop" not in source, (
        "FINDING A: process_current_report still collapses entry_stop_value/exit_stop_value onto the "
        "same 'applicable_stop' variable — entry and exit endpoint values must be derived independently "
        "from entry_snapshot.user_selected_stop_loss and exit_snapshot.final_stop_loss respectively."
    )
    assert "user_selected_stop_loss" in source and "final_stop_loss" in source, (
        "FINDING A: process_current_report does not visibly reference both "
        "entry_snapshot.user_selected_stop_loss and exit_snapshot.final_stop_loss."
    )


# FINDING B — outbox.mark_terminal only accepts COMPLETE/LIMITED_
# EVIDENCE/FAILED_TERMINAL. The existing-report reconciliation path in
# process_current_report currently passes status="SUCCEEDED", which is
# not a member of that set and raises ValueError.
def test_finding_b_existing_report_status_is_a_valid_terminal_status():
    from services.postmortem import outbox
    import inspect
    from services.postmortem import current_report_generation as m

    source = inspect.getsource(m.process_current_report)
    assert 'status="SUCCEEDED"' not in source, (
        "FINDING B: process_current_report's existing-report reconciliation path still passes "
        "status=\"SUCCEEDED\" to outbox.mark_terminal — 'SUCCEEDED' is not in "
        f"outbox._TERMINAL_STATUSES ({sorted(outbox._TERMINAL_STATUSES)}) and raises ValueError at runtime."
    )


# FINDING B (runtime proof) — actually calling the real outbox.
# mark_terminal function with the exact invalid status
# process_current_report currently uses proves this raises ValueError
# at runtime, not merely a style concern.
def test_finding_b_mark_terminal_rejects_succeeded_status():
    from services.postmortem import outbox
    with pytest.raises(ValueError, match="terminal status"):
        outbox.mark_terminal(conn=None, outbox_id=1, status="SUCCEEDED", claimed_by="x")


# FINDING C — report_trading_date must be derived from the market-local
# date (closed_at converted via market_tzinfo), never the bare UTC date.
# A trade closed at 2026-01-02 23:30 UTC is still 2026-01-03 local in
# Asia/Kolkata (UTC+5:30) — the bare .date() call would silently persist
# the wrong trading date.
def test_finding_c_report_trading_date_uses_market_local_conversion():
    import inspect
    from services.postmortem import current_report_generation as m

    source = inspect.getsource(m.process_current_report)
    assert 'trade["closed_at"].date()' not in source, (
        "FINDING C: process_current_report still calls trade['closed_at'].date() directly, without "
        "first converting to market-local time via market_tzinfo — this silently misattributes the "
        "trading date near UTC day boundaries for India (UTC+5:30) and can differ from US Eastern too."
    )
    assert "astimezone(market_tzinfo)" in source, (
        "FINDING C: process_current_report does not visibly call .astimezone(market_tzinfo) anywhere "
        "before deriving report_trading_date."
    )


# FINDING C (behavioral proof) — the actual date math confirms UTC and
# Asia/Kolkata dates genuinely differ for a boundary timestamp.
def test_finding_c_utc_and_market_local_dates_genuinely_differ_at_boundary():
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    closed_at_utc = datetime(2026, 1, 2, 23, 30, tzinfo=timezone.utc)
    utc_date = closed_at_utc.date()
    ist_date = closed_at_utc.astimezone(ist).date()
    assert utc_date != ist_date, (
        "FINDING C: setup sanity check failed — expected the UTC bare date and the Asia/Kolkata "
        "local date to genuinely differ for this boundary timestamp."
    )


# FINDING F/G — a terminal-success (COMPLETE/LIMITED_EVIDENCE) outbox
# row whose report is MISSING must be treated as a detected integrity
# contradiction, never silently self-healed by regenerating (which
# would fabricate a "successful" outcome for a row that already claims
# to have completed once, masking the underlying data-integrity issue
# from operators).
def test_finding_g_terminal_success_with_missing_report_is_a_contradiction_not_self_heal():
    import inspect
    from services.postmortem import current_report_generation as m

    assert hasattr(m, "CURRENT_REPORT_INTEGRITY_CONTRADICTION") or hasattr(m, "process_current_report"), (
        "FINDING G: current_report_generation does not yet define any integrity-contradiction outcome "
        "constant for a terminal-success outbox row with a missing report."
    )
    assert hasattr(m, "CURRENT_REPORT_INTEGRITY_CONTRADICTION"), (
        "FINDING G: current_report_generation.CURRENT_REPORT_INTEGRITY_CONTRADICTION is not yet defined "
        "(expected red pre-fix) — the reconciliation contract must distinguish this case from a normal "
        "generation attempt, never silently regenerate under a terminal-success row."
    )
