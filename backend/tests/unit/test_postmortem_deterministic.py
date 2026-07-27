"""
Unit tests for services/postmortem/deterministic.py — the Trade Postmortem
Engine's Phase 1 deterministic (no AI, no I/O) closed-trade calculation. No
DB, no HTTP, no network of any kind.
"""
import datetime as dt

import pytest

from services.postmortem.deterministic import (
    EVIDENCE_FIELDS,
    AutoCloseTimingEvidence,
    ClosedTradeRecord,
    EvidenceCompleteness,
    ExitMechanism,
    Outcome,
    compute_postmortem,
)

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
US_EASTERN_FIXED = dt.timezone(dt.timedelta(hours=-5))  # fixed offset stand-in, no DST logic needed for these tests


def _closed_trade(
    *,
    trade_id=1,
    symbol="AAPL",
    market="US",
    quantity=10,
    entry_price=100.0,
    exit_price=120.0,
    stop_loss=90.0,
    target_price=130.0,
    opened_at=dt.datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
    closed_at=dt.datetime(2026, 6, 2, 9, 30, tzinfo=UTC),
    trade_management_mode="manual",
    exit_reason="MANUAL",
    **provenance,
):
    return ClosedTradeRecord(
        trade_id=trade_id,
        status="CLOSED",
        symbol=symbol,
        market=market,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss=stop_loss,
        target_price=target_price,
        opened_at=opened_at,
        closed_at=closed_at,
        trade_management_mode=trade_management_mode,
        exit_reason=exit_reason,
        **provenance,
    )


FULL_PROVENANCE = dict(
    recommendation_source="daily_pick",
    daily_pick_run_id="run-1",
    daily_pick_rank=1,
    recommendation_generated_at=dt.datetime(2026, 5, 31, tzinfo=UTC),
    recommendation_reference_price=99.0,
    recommendation_entry_low=98.0,
    recommendation_entry_high=101.0,
    recommendation_original_stop_loss=90.0,
    recommendation_original_target=130.0,
    model_version="v1",
    execution_slippage_pct=1.01,
)


@pytest.mark.unit
class TestOutcomeClassification:
    def test_winning_long_trade(self):
        result = compute_postmortem(_closed_trade(entry_price=100.0, exit_price=120.0, quantity=10))
        assert result.outcome == Outcome.WIN
        assert result.realized_pnl_abs == pytest.approx(200.0)
        assert result.realized_pnl_pct == pytest.approx(20.0)

    def test_losing_long_trade(self):
        result = compute_postmortem(_closed_trade(entry_price=100.0, exit_price=90.0, quantity=10))
        assert result.outcome == Outcome.LOSS
        assert result.realized_pnl_abs == pytest.approx(-100.0)
        assert result.realized_pnl_pct == pytest.approx(-10.0)

    def test_exact_breakeven_trade(self):
        result = compute_postmortem(_closed_trade(entry_price=100.0, exit_price=100.0, quantity=10))
        assert result.outcome == Outcome.BREAKEVEN
        assert result.realized_pnl_abs == 0.0

    def test_breakeven_via_money_rounding_convention(self):
        """A sub-cent difference that rounds to 0.00 at the 2dp money
        precision this codebase uses everywhere (_trade_row_to_dict,
        paper_sell) must classify as BREAKEVEN, not a spurious WIN — the
        classification is defined against the already-rounded
        realized_pnl_abs, documented explicitly in deterministic.py."""
        result = compute_postmortem(_closed_trade(entry_price=100.00, exit_price=100.001, quantity=1))
        assert result.realized_pnl_abs == 0.0
        assert result.outcome == Outcome.BREAKEVEN

    def test_indeterminate_for_missing_entry_price(self):
        result = compute_postmortem(_closed_trade(entry_price=None))
        assert result.outcome == Outcome.INDETERMINATE
        assert result.realized_pnl_abs is None
        assert any("entry_price" in w for w in result.warnings)

    def test_indeterminate_for_missing_exit_price(self):
        result = compute_postmortem(_closed_trade(exit_price=None))
        assert result.outcome == Outcome.INDETERMINATE
        assert result.realized_pnl_abs is None
        assert any("exit_price" in w for w in result.warnings)

    def test_indeterminate_for_zero_quantity(self):
        result = compute_postmortem(_closed_trade(quantity=0))
        assert result.outcome == Outcome.INDETERMINATE
        assert any("quantity" in w for w in result.warnings)

    def test_indeterminate_for_negative_price_not_silently_accepted(self):
        result = compute_postmortem(_closed_trade(entry_price=-50.0))
        assert result.outcome == Outcome.INDETERMINATE
        assert result.realized_pnl_abs is None
        assert any("entry_price" in w for w in result.warnings)

    def test_open_trade_raises_rather_than_indeterminate(self):
        """An OPEN trade must never reach outcome classification at all —
        that's the API layer's 409 responsibility. This is a defensive
        contract check, not a normal INDETERMINATE outcome."""
        record = _closed_trade()
        record = ClosedTradeRecord(**{**record.__dict__, "status": "OPEN"})
        with pytest.raises(ValueError):
            compute_postmortem(record)


@pytest.mark.unit
class TestExitMechanism:
    def test_manual_close(self):
        result = compute_postmortem(_closed_trade(exit_reason="MANUAL", trade_management_mode="manual"))
        assert result.exit_mechanism == ExitMechanism.MANUAL
        assert result.auto_close_timing_evidence == AutoCloseTimingEvidence.NOT_APPLICABLE

    def test_automatic_target_close(self):
        result = compute_postmortem(_closed_trade(exit_reason="TARGET_HIT", trade_management_mode="auto"))
        assert result.exit_mechanism == ExitMechanism.TARGET_HIT

    def test_automatic_stop_loss_close(self):
        result = compute_postmortem(_closed_trade(exit_reason="STOP_LOSS", trade_management_mode="auto"))
        assert result.exit_mechanism == ExitMechanism.STOP_LOSS

    def test_unknown_legacy_exit_reason_handled_safely(self):
        """A stored exit_reason this module doesn't recognize (e.g. a
        legacy/pre-column value) must not crash — UNKNOWN plus the
        original stored value, never guessed into a known bucket."""
        result = compute_postmortem(_closed_trade(exit_reason="SOME_FUTURE_VALUE"))
        assert result.exit_mechanism == ExitMechanism.UNKNOWN
        assert result.exit_mechanism_raw == "SOME_FUTURE_VALUE"

    def test_null_exit_reason_is_unknown_not_manual(self):
        result = compute_postmortem(_closed_trade(exit_reason=None))
        assert result.exit_mechanism == ExitMechanism.UNKNOWN

    def test_non_string_exit_reason_is_sanitized_to_none(self):
        """Stage 0 audit fix: exit_reason is a TEXT column, so a real DB row
        can never hand this a non-string — but the typed boundary must not
        surface a raw non-string object as if it were a safely-displayable
        stored value. A ClosedTradeRecord built with a non-string
        exit_reason (defensive-boundary case, not a real DB scenario) must
        report exit_mechanism_raw=None, not the raw object."""
        result = compute_postmortem(_closed_trade(exit_reason=12345))
        assert result.exit_mechanism == ExitMechanism.UNKNOWN
        assert result.exit_mechanism_raw is None
        assert result.exit_mechanism_raw is None


@pytest.mark.unit
class TestAutoCloseTimingEvidence:
    def test_browser_triggered_target_hit_is_client_reported_unverified(self):
        result = compute_postmortem(_closed_trade(exit_reason="TARGET_HIT", trade_management_mode="auto"))
        assert result.auto_close_timing_evidence == AutoCloseTimingEvidence.CLIENT_REPORTED_UNVERIFIED

    def test_browser_triggered_stop_loss_is_client_reported_unverified(self):
        result = compute_postmortem(_closed_trade(exit_reason="STOP_LOSS", trade_management_mode="auto"))
        assert result.auto_close_timing_evidence == AutoCloseTimingEvidence.CLIENT_REPORTED_UNVERIFIED

    def test_manual_close_is_not_applicable_regardless_of_management_mode(self):
        result = compute_postmortem(_closed_trade(exit_reason="MANUAL", trade_management_mode="auto"))
        assert result.auto_close_timing_evidence == AutoCloseTimingEvidence.NOT_APPLICABLE

    def test_manual_mode_trade_is_not_applicable(self):
        result = compute_postmortem(_closed_trade(exit_reason="TARGET_HIT", trade_management_mode="manual"))
        # A TARGET_HIT exit_reason on a manual-mode trade shouldn't occur in
        # practice (the client only sends exit_reason on an auto-close
        # trigger), but if it did, this module never asserts an auto-close
        # timing claim for a manual-mode trade.
        assert result.auto_close_timing_evidence == AutoCloseTimingEvidence.NOT_APPLICABLE

    def test_server_verified_is_never_returned(self):
        for mgmt_mode in ("manual", "auto"):
            for reason in ("TARGET_HIT", "STOP_LOSS", "MANUAL", None, "WEIRD"):
                result = compute_postmortem(_closed_trade(trade_management_mode=mgmt_mode, exit_reason=reason))
                assert result.auto_close_timing_evidence != AutoCloseTimingEvidence.SERVER_VERIFIED


@pytest.mark.unit
class TestEvidenceCompleteness:
    def test_no_provenance_is_limited(self):
        result = compute_postmortem(_closed_trade())
        assert result.evidence_completeness == EvidenceCompleteness.LIMITED
        assert result.available_evidence_fields == []
        assert result.missing_evidence_fields == sorted(EVIDENCE_FIELDS)

    def test_partial_provenance_is_partial(self):
        result = compute_postmortem(_closed_trade(recommendation_source="daily_pick", model_version="v1"))
        assert result.evidence_completeness == EvidenceCompleteness.PARTIAL
        assert result.available_evidence_fields == sorted(["recommendation_source", "model_version"])
        assert "recommendation_source" not in result.missing_evidence_fields
        assert "daily_pick_run_id" in result.missing_evidence_fields

    def test_full_provenance_is_complete(self):
        result = compute_postmortem(_closed_trade(**FULL_PROVENANCE))
        assert result.evidence_completeness == EvidenceCompleteness.COMPLETE
        assert result.available_evidence_fields == sorted(EVIDENCE_FIELDS)
        assert result.missing_evidence_fields == []

    def test_signal_override_and_levels_modified_are_not_evidence_fields(self):
        # These two columns exist on paper_trades but are deliberately
        # excluded from evidence-completeness scoring (see module docstring).
        assert "signal_override" not in EVIDENCE_FIELDS
        assert "levels_modified_after_entry" not in EVIDENCE_FIELDS


@pytest.mark.unit
class TestHoldingDuration:
    def test_overnight_holding(self):
        result = compute_postmortem(_closed_trade(
            opened_at=dt.datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
            closed_at=dt.datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
        ))
        assert result.holding_duration_seconds == pytest.approx(19 * 3600)

    def test_timezone_aware_duration_ist_vs_utc(self):
        """IST is UTC+5:30 — opened in IST, closed in UTC; the duration must
        be computed correctly across the offset without stripping tzinfo."""
        opened = dt.datetime(2026, 6, 1, 15, 0, tzinfo=IST)   # == 09:30 UTC
        closed = dt.datetime(2026, 6, 1, 11, 30, tzinfo=UTC)  # 2h after 09:30 UTC
        result = compute_postmortem(_closed_trade(opened_at=opened, closed_at=closed))
        assert result.holding_duration_seconds == pytest.approx(2 * 3600)

    def test_in_and_us_timestamps_crossing_utc_calendar_boundary(self):
        """An IN trade opened late in the IST evening can fall on the NEXT
        UTC calendar date than its own local date; a US trade opened in the
        US morning can fall on the PREVIOUS UTC calendar date. The duration
        must still be correct in both directions regardless of each side's
        local calendar date."""
        # IST 23:45 on June 1 == UTC 18:15 on June 1 (no UTC date rollover
        # here, but exercises a large positive offset near a date boundary).
        in_opened = dt.datetime(2026, 6, 1, 23, 45, tzinfo=IST)
        in_closed = dt.datetime(2026, 6, 2, 0, 15, tzinfo=IST)  # 30 min later, local date DID roll over
        in_result = compute_postmortem(_closed_trade(opened_at=in_opened, closed_at=in_closed, market="IN"))
        assert in_result.holding_duration_seconds == pytest.approx(30 * 60)

        # US Eastern 20:00 on June 1 == UTC 01:00 on June 2 — UTC date is
        # AHEAD of the US local date.
        us_opened = dt.datetime(2026, 6, 1, 20, 0, tzinfo=US_EASTERN_FIXED)
        us_closed = dt.datetime(2026, 6, 1, 21, 30, tzinfo=US_EASTERN_FIXED)
        us_result = compute_postmortem(_closed_trade(opened_at=us_opened, closed_at=us_closed, market="US"))
        assert us_result.holding_duration_seconds == pytest.approx(90 * 60)

    def test_malformed_negative_duration_is_indeterminate(self):
        result = compute_postmortem(_closed_trade(
            opened_at=dt.datetime(2026, 6, 2, tzinfo=UTC),
            closed_at=dt.datetime(2026, 6, 1, tzinfo=UTC),
        ))
        assert result.holding_duration_seconds is None
        assert result.outcome == Outcome.INDETERMINATE
        assert any("negative holding duration" in w for w in result.warnings)

    def test_missing_opened_at_is_indeterminate(self):
        result = compute_postmortem(_closed_trade(opened_at=None))
        assert result.holding_duration_seconds is None
        assert result.outcome == Outcome.INDETERMINATE

    def test_missing_closed_at_is_indeterminate(self):
        result = compute_postmortem(_closed_trade(closed_at=None))
        assert result.holding_duration_seconds is None
        assert result.outcome == Outcome.INDETERMINATE


@pytest.mark.unit
class TestTargetAndStopDistance:
    def test_exit_above_target_is_positive_distance(self):
        result = compute_postmortem(_closed_trade(exit_price=143.0, target_price=130.0))
        assert result.target_distance_at_exit_pct == pytest.approx(10.0)

    def test_exit_below_target_is_negative_distance(self):
        result = compute_postmortem(_closed_trade(exit_price=117.0, target_price=130.0))
        assert result.target_distance_at_exit_pct == pytest.approx(-10.0)

    def test_exit_above_stop_is_positive_distance(self):
        result = compute_postmortem(_closed_trade(exit_price=99.0, stop_loss=90.0))
        assert result.stop_distance_at_exit_pct == pytest.approx(10.0)

    def test_exit_below_stop_is_negative_distance(self):
        result = compute_postmortem(_closed_trade(exit_price=81.0, stop_loss=90.0))
        assert result.stop_distance_at_exit_pct == pytest.approx(-10.0)

    def test_null_target_returns_null_distance(self):
        result = compute_postmortem(_closed_trade(target_price=None))
        assert result.target_distance_at_exit_pct is None

    def test_null_stop_returns_null_distance(self):
        result = compute_postmortem(_closed_trade(stop_loss=None))
        assert result.stop_distance_at_exit_pct is None

    def test_zero_target_returns_null_distance_not_zerodivisionerror(self):
        result = compute_postmortem(_closed_trade(target_price=0.0))
        assert result.target_distance_at_exit_pct is None

    def test_negative_stop_returns_null_distance(self):
        result = compute_postmortem(_closed_trade(stop_loss=-10.0))
        assert result.stop_distance_at_exit_pct is None

    def test_distance_never_asserts_a_crossing(self):
        """A positive/negative distance is purely a price comparison, not a
        claim that the level was actually crossed during the trade's life —
        that claim only comes from exit_mechanism (the stored exit_reason
        evidence). A MANUAL exit that happens to land past the target must
        not be reported as exit_mechanism TARGET_HIT."""
        result = compute_postmortem(_closed_trade(
            exit_price=200.0, target_price=130.0, exit_reason="MANUAL",
        ))
        assert result.target_distance_at_exit_pct == pytest.approx(53.85, abs=0.01)
        assert result.exit_mechanism == ExitMechanism.MANUAL


@pytest.mark.unit
class TestCalculationVersionAndNoNetwork:
    def test_calculation_version_is_stamped(self):
        result = compute_postmortem(_closed_trade())
        assert result.calculation_version

    def test_module_has_no_network_or_ai_dependency(self):
        """Static guard: the deterministic module must never import a
        network/HTTP/AI client — Phase 1 is local computation only."""
        import pathlib
        import services.postmortem.deterministic as mod
        source = pathlib.Path(mod.__file__).read_text()
        forbidden = ["requests", "httpx", "anthropic", "openai", "urllib", "psycopg"]
        for token in forbidden:
            assert token not in source, f"unexpected dependency token {token!r} found in deterministic.py"
