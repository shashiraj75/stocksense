"""
Unit tests for services/postmortem/causal_analysis.py — the Trade Postmortem
Engine's Stage 3 deterministic causal-analysis layer. No DB, no HTTP, no
network of any kind; every assertion here is against a pure function.
"""
import datetime as dt

import pytest

from services.postmortem.causal_analysis import (
    CAUSAL_CALCULATION_VERSION,
    RootCauseCategory,
    SignalDirectionAgreement,
    ThesisAssessment,
    build_causal_analysis,
)
from services.postmortem.deterministic import (
    ClosedTradeRecord,
    compute_postmortem,
)
from services.postmortem.entry_snapshot import (
    EntrySnapshot,
    RecommendationContext,
    build_entry_snapshot,
)

UTC = dt.timezone.utc


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
    )


def _snapshot(
    *,
    trade_id=1,
    technical_signal=None,
    recommendation_signal=None,
    fundamental_score=None,
    sentiment_score=None,
    market_regime_trend=None,
    execution_range_position_inputs=(None, None, None),
) -> EntrySnapshot:
    exec_price, entry_low, entry_high = execution_range_position_inputs
    ctx = RecommendationContext(
        evidence_source="DAILY_PICK",
        simulated_execution_price=exec_price if exec_price is not None else 100.0,
        technical_signal=technical_signal,
        recommendation_signal=recommendation_signal,
        fundamental_score=fundamental_score,
        sentiment_score=sentiment_score,
        market_regime_trend=market_regime_trend,
        recommendation_entry_low=entry_low,
        recommendation_entry_high=entry_high,
    )
    return build_entry_snapshot(paper_trade_id=trade_id, user_id="user-1", symbol="AAPL", market="US", ctx=ctx)


class TestEntryConditions:
    def test_no_snapshot_is_insufficient_evidence(self):
        pm = compute_postmortem(_closed_trade())
        narrative = build_causal_analysis(pm, None)
        assert narrative.entry_conditions == []
        assert narrative.entry_conditions_reason == "Insufficient evidence to determine this factor reliably."

    def test_populated_snapshot_lists_facts(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(technical_signal="BUY", fundamental_score=70.0)
        narrative = build_causal_analysis(pm, snap)
        assert narrative.entry_conditions_reason is None
        assert any("Technical signal" in f for f in narrative.entry_conditions)
        assert any("Fundamental score" in f for f in narrative.entry_conditions)


class TestSignalFactors:
    def test_win_with_bullish_technical_signal_agrees(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        tech = next(s for s in narrative.signal_factors if s.factor == "technical_signal")
        assert tech.agreement == SignalDirectionAgreement.AGREED

    def test_loss_with_bullish_technical_signal_contradicts(self):
        pm = compute_postmortem(_closed_trade(exit_price=80.0))
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        tech = next(s for s in narrative.signal_factors if s.factor == "technical_signal")
        assert tech.agreement == SignalDirectionAgreement.CONTRADICTED

    def test_missing_signal_field_is_insufficient_evidence(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot()
        narrative = build_causal_analysis(pm, snap)
        tech = next(s for s in narrative.signal_factors if s.factor == "technical_signal")
        assert tech.agreement == SignalDirectionAgreement.INSUFFICIENT_EVIDENCE

    def test_breakeven_outcome_never_produces_directional_agreement(self):
        pm = compute_postmortem(_closed_trade(entry_price=100.0, exit_price=100.0))
        assert pm.outcome.value == "BREAKEVEN"
        snap = _snapshot(technical_signal="BUY", recommendation_signal="BUY", fundamental_score=90.0, sentiment_score=90.0)
        narrative = build_causal_analysis(pm, snap)
        for factor in narrative.signal_factors:
            assert factor.agreement == SignalDirectionAgreement.INSUFFICIENT_EVIDENCE

    def test_indeterminate_outcome_never_produces_directional_agreement(self):
        pm = compute_postmortem(_closed_trade(entry_price=None))
        assert pm.outcome.value == "INDETERMINATE"
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        for factor in narrative.signal_factors:
            assert factor.agreement == SignalDirectionAgreement.INSUFFICIENT_EVIDENCE


class TestPriceMoveCause:
    def test_reuses_first_agreed_signal_never_recomputes(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.price_move_cause.agreement == SignalDirectionAgreement.AGREED

    def test_no_directional_signal_is_insufficient_evidence(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        narrative = build_causal_analysis(pm, None)
        assert narrative.price_move_cause.agreement == SignalDirectionAgreement.INSUFFICIENT_EVIDENCE


class TestMarketContext:
    def test_sector_news_volatility_liquidity_always_insufficient_evidence(self):
        pm = compute_postmortem(_closed_trade())
        snap = _snapshot(market_regime_trend="BULLISH")
        narrative = build_causal_analysis(pm, snap)
        ctx = narrative.market_context
        for factor in (ctx.sector, ctx.news, ctx.volatility, ctx.liquidity):
            assert factor.agreement == SignalDirectionAgreement.INSUFFICIENT_EVIDENCE

    def test_no_snapshot_all_six_subfactors_insufficient(self):
        pm = compute_postmortem(_closed_trade())
        narrative = build_causal_analysis(pm, None)
        ctx = narrative.market_context
        for factor in (ctx.regime, ctx.timing, ctx.sector, ctx.news, ctx.volatility, ctx.liquidity):
            assert factor.agreement == SignalDirectionAgreement.INSUFFICIENT_EVIDENCE


class TestThesisAssessment:
    def test_all_agreed_win_is_correct(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(technical_signal="BUY", recommendation_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.thesis_assessment == ThesisAssessment.CORRECT

    def test_all_contradicted_loss_is_unsupported(self):
        pm = compute_postmortem(_closed_trade(exit_price=80.0))
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.thesis_assessment == ThesisAssessment.UNSUPPORTED

    def test_mixed_signals_is_partially_correct(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        snap = _snapshot(technical_signal="BUY", recommendation_signal="SELL")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.thesis_assessment == ThesisAssessment.PARTIALLY_CORRECT

    def test_zero_assessable_signals_is_insufficient_evidence(self):
        pm = compute_postmortem(_closed_trade(exit_price=120.0))
        narrative = build_causal_analysis(pm, None)
        assert narrative.thesis_assessment == ThesisAssessment.INSUFFICIENT_EVIDENCE


class TestRootCauseOrdering:
    def test_no_evidence_at_all_is_insufficient_evidence(self):
        pm = compute_postmortem(_closed_trade(exit_reason=None))
        narrative = build_causal_analysis(pm, None)
        assert narrative.root_cause == RootCauseCategory.INSUFFICIENT_EVIDENCE

    def test_manual_loss_closer_to_target_is_position_management(self):
        # stop far below (10), target barely above entry (101): the exit
        # (95) sits much closer to the target level than the stop level,
        # even though it's a loss — evidence the position was closed well
        # before the stop was ever threatened.
        pm = compute_postmortem(
            _closed_trade(exit_reason="MANUAL", exit_price=95.0, stop_loss=10.0, target_price=101.0)
        )
        assert pm.outcome.value == "LOSS"
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.root_cause == RootCauseCategory.POSITION_MANAGEMENT

    def test_stop_loss_with_bullish_signals_is_market_conditions(self):
        """Bullish entry signals + a LOSS outcome means the signals are
        CONTRADICTED (measured against the realized outcome) — a reasonable
        entry that a real stop-loss then closed out, not a flawed pick."""
        pm = compute_postmortem(_closed_trade(exit_reason="STOP_LOSS", exit_price=90.0))
        snap = _snapshot(technical_signal="BUY", recommendation_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.root_cause == RootCauseCategory.MARKET_CONDITIONS

    def test_unsupported_thesis_is_selection(self):
        pm = compute_postmortem(_closed_trade(exit_reason="MANUAL", exit_price=80.0, stop_loss=None, target_price=None))
        snap = _snapshot(technical_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert narrative.thesis_assessment == ThesisAssessment.UNSUPPORTED
        assert narrative.root_cause == RootCauseCategory.SELECTION

    def test_unknown_exit_mechanism_non_win_is_exit_logic(self):
        # market_regime_trend alone (a non-directional field) keeps evidence
        # completeness above LIMITED, so Rule 1 (no evidence at all) doesn't
        # pre-empt Rule 6 (unknown exit mechanism) — no directional signal
        # is supplied, so `assessable` is still empty either way.
        snap = _snapshot(market_regime_trend="BULLISH")
        pm = compute_postmortem(
            _closed_trade(exit_reason="SOMETHING_ELSE", exit_price=80.0, stop_loss=None, target_price=None),
            snapshot=snap,
        )
        narrative = build_causal_analysis(pm, snap)
        assert narrative.root_cause == RootCauseCategory.EXIT_LOGIC


class TestTakeaway:
    def test_insufficient_evidence_root_cause_has_generic_takeaway(self):
        pm = compute_postmortem(_closed_trade(exit_reason=None))
        narrative = build_causal_analysis(pm, None)
        assert "not enough stored evidence" in narrative.takeaway.lower()

    def test_never_uses_forbidden_certainty_language(self):
        """Governing rule: hedge language only, never assert proof."""
        pm = compute_postmortem(_closed_trade(exit_reason="STOP_LOSS", exit_price=90.0))
        snap = _snapshot(technical_signal="BUY", recommendation_signal="BUY")
        narrative = build_causal_analysis(pm, snap)
        assert "proves" not in narrative.takeaway.lower()


def test_calculation_version_format():
    assert CAUSAL_CALCULATION_VERSION.count(".") == 2
