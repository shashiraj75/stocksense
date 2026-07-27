"""
Unit tests for services/postmortem/entry_snapshot.py — Trade Postmortem
Engine, Stage 2. Pure functions only: no DB, no HTTP.
"""
import datetime as dt

import pytest

from services.postmortem.entry_snapshot import (
    EvidenceSource,
    RangePosition,
    RecommendationContext,
    VerificationLevel,
    VERIFIABLE_EVIDENCE_FIELDS,
    build_entry_snapshot,
    classify_evidence_completeness,
)

UTC = dt.timezone.utc


def _ctx(**over):
    d = dict(
        evidence_source=EvidenceSource.MANUAL.value,
        simulated_execution_price=100.0,
    )
    d.update(over)
    return RecommendationContext(**d)


@pytest.mark.unit
class TestExecutionSlippage:
    def test_positive_slippage_execution_above_reference(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(simulated_execution_price=110.0, recommendation_reference_price=100.0),
        )
        assert snap.execution_slippage_pct == pytest.approx(10.0)

    def test_negative_slippage_execution_below_reference(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(simulated_execution_price=90.0, recommendation_reference_price=100.0),
        )
        assert snap.execution_slippage_pct == pytest.approx(-10.0)

    def test_none_reference_price_gives_none_slippage(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=_ctx(),
        )
        assert snap.execution_slippage_pct is None

    def test_zero_reference_price_gives_none_not_zerodivisionerror(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_reference_price=0.0),
        )
        assert snap.execution_slippage_pct is None

    def test_negative_reference_price_gives_none(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_reference_price=-5.0),
        )
        assert snap.execution_slippage_pct is None


@pytest.mark.unit
class TestExecutionRangePosition:
    def test_within_range(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(simulated_execution_price=100.0, recommendation_entry_low=95.0, recommendation_entry_high=105.0),
        )
        assert snap.execution_range_position == RangePosition.WITHIN_RANGE.value

    def test_below_range(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(simulated_execution_price=90.0, recommendation_entry_low=95.0, recommendation_entry_high=105.0),
        )
        assert snap.execution_range_position == RangePosition.BELOW_RANGE.value

    def test_above_range(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(simulated_execution_price=110.0, recommendation_entry_low=95.0, recommendation_entry_high=105.0),
        )
        assert snap.execution_range_position == RangePosition.ABOVE_RANGE.value

    def test_unknown_when_no_range_available(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=_ctx(),
        )
        assert snap.execution_range_position == RangePosition.UNKNOWN.value

    def test_unknown_when_only_low_present(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_entry_low=95.0),
        )
        assert snap.execution_range_position == RangePosition.UNKNOWN.value

    def test_unknown_when_low_exceeds_high(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_entry_low=110.0, recommendation_entry_high=90.0),
        )
        assert snap.execution_range_position == RangePosition.UNKNOWN.value

    def test_unknown_when_range_is_non_positive(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_entry_low=-5.0, recommendation_entry_high=10.0),
        )
        assert snap.execution_range_position == RangePosition.UNKNOWN.value


@pytest.mark.unit
class TestRewardToRiskRatio:
    def test_computed_when_all_inputs_valid(self):
        # reference=100, stop=90 (risk=10), target=130 (reward=30) -> 3.0
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(
                recommendation_reference_price=100.0,
                recommended_stop_loss=90.0,
                recommended_target_price=130.0,
            ),
        )
        assert snap.reward_to_risk_ratio == pytest.approx(3.0)

    def test_none_when_stop_at_or_above_reference(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_reference_price=100.0, recommended_stop_loss=100.0, recommended_target_price=130.0),
        )
        assert snap.reward_to_risk_ratio is None

    def test_none_when_any_input_missing(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_reference_price=100.0, recommended_stop_loss=90.0),
        )
        assert snap.reward_to_risk_ratio is None

    def test_none_when_stop_negative(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommendation_reference_price=100.0, recommended_stop_loss=-10.0, recommended_target_price=130.0),
        )
        assert snap.reward_to_risk_ratio is None


@pytest.mark.unit
class TestUserOverrodeRecommendation:
    def test_none_when_no_recommendation_to_compare(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(user_selected_stop_loss=90.0),
        )
        assert snap.user_overrode_recommendation is None

    def test_false_when_user_selected_matches_recommendation(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommended_stop_loss=90.0, user_selected_stop_loss=90.0,
                     recommended_target_price=130.0, user_selected_target_price=130.0),
        )
        assert snap.user_overrode_recommendation is False

    def test_true_when_stop_differs(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommended_stop_loss=90.0, user_selected_stop_loss=85.0),
        )
        assert snap.user_overrode_recommendation is True

    def test_true_when_target_differs(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommended_target_price=130.0, user_selected_target_price=140.0),
        )
        assert snap.user_overrode_recommendation is True

    def test_tiny_float_noise_within_a_cent_is_not_an_override(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(recommended_stop_loss=90.0, user_selected_stop_loss=90.001),
        )
        assert snap.user_overrode_recommendation is False


@pytest.mark.unit
class TestVerificationLevels:
    def test_available_field_is_client_reported(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(confidence_score=72.0),
        )
        assert snap.verification_levels["confidence_score"] == VerificationLevel.CLIENT_REPORTED.value

    def test_missing_field_is_unavailable(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=_ctx(),
        )
        assert snap.verification_levels["confidence_score"] == VerificationLevel.UNAVAILABLE.value

    def test_server_verified_never_appears(self):
        """No server-side lookup path exists yet — see module docstring.
        This must remain true even when every field is populated."""
        full_ctx = _ctx(
            recommendation_signal="BUY", recommendation_generated_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
            recommendation_reference_price=100.0, recommendation_entry_low=95.0, recommendation_entry_high=105.0,
            recommended_stop_loss=90.0, recommended_target_price=130.0, confidence_score=80.0,
            technical_signal="BUY", technical_rsi=55.0, technical_macd_diff=0.5,
            fundamental_score=70.0, sentiment_score=0.6, sentiment_label="bullish",
            market_regime_trend="bull", market_regime_score_adj=1.1, market_regime_reason="uptrend",
            recommendation_reasoning=[{"indicator": "RSI", "signal": "BUY", "reason": "oversold"}],
            daily_pick_run_id="run-1", daily_pick_rank=1, model_version="v1",
        )
        snap = build_entry_snapshot(paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=full_ctx)
        assert VerificationLevel.SERVER_VERIFIED.value not in snap.verification_levels.values()

    def test_every_verifiable_field_is_labeled(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=_ctx(),
        )
        assert set(snap.verification_levels.keys()) == set(VERIFIABLE_EVIDENCE_FIELDS)


@pytest.mark.unit
class TestEvidenceCompleteness:
    def test_limited_when_nothing_reported(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=_ctx(),
        )
        completeness, available, missing = classify_evidence_completeness(snap)
        assert completeness == "LIMITED"
        assert available == []
        assert missing == sorted(VERIFIABLE_EVIDENCE_FIELDS)

    def test_partial_when_some_reported(self):
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(confidence_score=80.0, technical_signal="BUY"),
        )
        completeness, available, missing = classify_evidence_completeness(snap)
        assert completeness == "PARTIAL"
        assert available == sorted(["confidence_score", "technical_signal"])

    def test_complete_when_every_verifiable_field_reported(self):
        full_ctx = _ctx(
            recommendation_signal="BUY", recommendation_generated_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
            recommendation_reference_price=100.0, recommendation_entry_low=95.0, recommendation_entry_high=105.0,
            recommended_stop_loss=90.0, recommended_target_price=130.0, confidence_score=80.0,
            technical_signal="BUY", technical_rsi=55.0, technical_macd_diff=0.5,
            fundamental_score=70.0, sentiment_score=0.6, sentiment_label="bullish",
            market_regime_trend="bull", market_regime_score_adj=1.1, market_regime_reason="uptrend",
            recommendation_reasoning=[{"indicator": "RSI", "signal": "BUY", "reason": "oversold"}],
            daily_pick_run_id="run-1", daily_pick_rank=1, model_version="v1",
        )
        snap = build_entry_snapshot(paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=full_ctx)
        completeness, available, missing = classify_evidence_completeness(snap)
        assert completeness == "COMPLETE"
        assert missing == []

    def test_complete_presence_never_implies_server_verified_trust(self):
        """Migration-verification hardening gate, Part 8 — the exact
        scenario the report must make unmistakable: a trade can be
        COMPLETE in field presence while every single field remains
        CLIENT_REPORTED. Completeness and verification are two separate
        API fields precisely so this case can never be misread as
        'independently confirmed'."""
        full_ctx = _ctx(
            recommendation_signal="BUY", recommendation_generated_at=dt.datetime(2026, 1, 1, tzinfo=UTC),
            recommendation_reference_price=100.0, recommendation_entry_low=95.0, recommendation_entry_high=105.0,
            recommended_stop_loss=90.0, recommended_target_price=130.0, confidence_score=80.0,
            technical_signal="BUY", technical_rsi=55.0, technical_macd_diff=0.5,
            fundamental_score=70.0, sentiment_score=0.6, sentiment_label="bullish",
            market_regime_trend="bull", market_regime_score_adj=1.1, market_regime_reason="uptrend",
            recommendation_reasoning=[{"indicator": "RSI", "signal": "BUY", "reason": "oversold"}],
            daily_pick_run_id="run-1", daily_pick_rank=1, model_version="v1",
        )
        snap = build_entry_snapshot(paper_trade_id=1, user_id="u1", symbol="AAPL", market="US", ctx=full_ctx)
        completeness, _, _ = classify_evidence_completeness(snap)
        assert completeness == "COMPLETE"
        assert all(level == VerificationLevel.CLIENT_REPORTED.value for level in snap.verification_levels.values())
        assert VerificationLevel.SERVER_VERIFIED.value not in snap.verification_levels.values()


@pytest.mark.unit
class TestSnapshotLinkage:
    def test_snapshot_carries_the_exact_trade_symbol_and_market(self):
        snap = build_entry_snapshot(
            paper_trade_id=42, user_id="user-1", symbol="MSFT", market="US", ctx=_ctx(),
        )
        assert snap.paper_trade_id == 42
        assert snap.user_id == "user-1"
        assert snap.symbol == "MSFT"
        assert snap.market == "US"

    def test_manual_evidence_source_with_no_recommendation_is_a_valid_limited_snapshot(self):
        """Part 8 — a manual trade must not fail solely because no
        recommendation exists; it gets an honest LIMITED snapshot."""
        snap = build_entry_snapshot(
            paper_trade_id=1, user_id="u1", symbol="AAPL", market="US",
            ctx=_ctx(evidence_source=EvidenceSource.MANUAL.value),
        )
        assert snap.evidence_source == "MANUAL"
        completeness, _, _ = classify_evidence_completeness(snap)
        assert completeness == "LIMITED"
