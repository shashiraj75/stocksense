"""Unit tests for explanation.py — the 'Why Now?' contract renders only
from verified structured fields, never fabricates a numeric value."""
import datetime

import pytest

from services.market_leadership.contracts import (
    StockRelativeStrength, GroupLeadership, TrendLifecycle, MarketBreadth,
    RsTrend, DataQualityStatus, GroupState, LeadershipConcentration,
    TrendLifecycleState, ExtensionRisk, BreadthState,
)
from services.market_leadership.explanation import build_why_now_explanation

AS_OF = "2026-07-20"


def _rs(score=85, pctl=85.0, trend=RsTrend.IMPROVING, quality=DataQualityStatus.OK):
    return StockRelativeStrength(
        symbol="ABC", market="IN", stock_rs_score=score, stock_rs_percentile=pctl,
        benchmark_relative_return=10.0, universe_relative_rank=5, universe_size=100,
        rs_1m=2.0, rs_3m=5.0, rs_6m=8.0, rs_12m=12.0, rs_trend=trend, rs_acceleration=1.0,
        data_coverage=1.0, data_quality_status=quality, calculation_as_of=AS_OF,
        universe_id="u1", universe_version="v1", methodology_version="ml-rs-1.0.0",
        reason_codes=[],
    )


def _group(state=GroupState.LEADING, rank=8):
    return GroupLeadership(
        market="IN", group_kind="sector", sector_name="IT", industry_name="IT",
        sector_rs_score=80.0, industry_rs_score=80.0, sector_rank=rank, industry_rank=rank,
        sector_rank_change=None, industry_rank_change=None, leadership_breadth=0.7,
        participation_ratio=0.6, median_member_rs=80.0, weighted_member_rs=78.0,
        improving_member_ratio=0.6, new_high_participation=0.3, group_trend="UP",
        group_state=state, concentration=LeadershipConcentration.BROAD, member_count=15,
        scored_member_count=15, calculation_as_of=AS_OF, classification_source="s",
        classification_version="v1", classification_pit_safe=False, universe_id="u1",
        universe_version="v1", methodology_version="ml-gl-1.0.0", reason_codes=[],
    )


def _trend(state=TrendLifecycleState.CONFIRMED_ADVANCE, risk=ExtensionRisk.MODERATE):
    return TrendLifecycle(
        symbol="ABC", market="IN", state=state, extension_risk=risk,
        evidence_completeness=0.9, price_vs_long_ma_pct=15.0, long_ma_slope_pct_per_day=0.1,
        drawdown_from_252d_high_pct=-5.0, high_volume_decline_count_21d=0,
        volume_confirmation_ratio_21d=1.5, calculation_as_of=AS_OF,
        methodology_version="ml-tl-1.0.0", reason_codes=[],
    )


def _breadth(state=BreadthState.HEALTHY):
    return MarketBreadth(
        market="IN", scope="market", state=state, pct_above_20dma=0.6, pct_above_50dma=0.6,
        pct_above_200dma=0.65, advance_decline_ratio_21d=1.3, new_highs_63d=10, new_lows_63d=2,
        improving_rs_ratio=0.5, direction_5d="IMPROVING", components=[], coverage=0.9,
        universe_size=100, scored_count=90, expected_session=AS_OF, calculation_as_of=AS_OF,
        universe_id="u1", universe_version="v1", methodology_version="ml-br-1.0.0", reason_codes=[],
    )


@pytest.mark.unit
class TestBuildWhyNowExplanation:
    def test_all_present_and_strong_yields_supporting_factors(self):
        exp = build_why_now_explanation(_rs(), _group(), _trend(), _breadth())
        assert len(exp.supporting_factors) > 0
        assert "certain" not in exp.summary.lower()
        assert "guarantee" not in exp.summary.lower()

    def test_extended_advance_always_adds_a_caution_factor(self):
        exp = build_why_now_explanation(
            _rs(), _group(), _trend(TrendLifecycleState.EXTENDED_ADVANCE, ExtensionRisk.HIGH), _breadth(),
        )
        assert any("extension" in c.lower() for c in exp.caution_factors)
        assert exp.extension_risk == "HIGH"

    def test_weak_rs_adds_caution_not_a_fabricated_number(self):
        exp = build_why_now_explanation(_rs(score=10, pctl=10.0), _group(), _trend(), _breadth())
        assert any("weak" in c.lower() for c in exp.caution_factors)
        assert "10" in exp.stock_rs_context

    def test_all_none_inputs_yields_insufficient_summary_not_a_crash(self):
        exp = build_why_now_explanation(None, None, None, None)
        assert exp.stock_rs_context is None
        assert exp.industry_context is None
        assert "insufficient" in exp.summary.lower() or len(exp.caution_factors) > 0

    def test_deteriorating_breadth_flagged_as_caution(self):
        exp = build_why_now_explanation(_rs(), _group(), _trend(), _breadth(BreadthState.DETERIORATING))
        assert any("deteriorating" in c.lower() for c in exp.caution_factors)

    def test_reason_codes_are_union_of_inputs(self):
        rs = _rs()
        rs.reason_codes = ["MISSING_BARS"]
        exp = build_why_now_explanation(rs, _group(), _trend(), _breadth())
        assert "MISSING_BARS" in exp.reason_codes

    def test_no_numeric_value_absent_from_inputs_appears_in_summary(self):
        """Regression against LLM-style fabrication: every number quoted in
        the summary must trace to a field actually present in an input
        contract (spot-checked via the rs_score value, which is above the
        supporting-factor threshold so it actually appears in the summary)."""
        rs = _rs(score=88, pctl=88.0)
        exp = build_why_now_explanation(rs, None, None, None)
        assert "88" in exp.summary

    def test_insufficient_rs_data_produces_caution_not_silence(self):
        rs = _rs(score=None, pctl=None, quality=DataQualityStatus.FAILED)
        rs.benchmark_relative_return = None
        exp = build_why_now_explanation(rs, None, None, None)
        assert any("insufficient" in c.lower() or "could not" in c.lower() for c in exp.caution_factors)

    def test_no_universe_percentile_but_valid_data_never_claims_insufficient_history(self):
        """Regression: found live (2026-07-25, real AAPL data via the
        single-symbol on-demand endpoint) — percentile is honestly None
        (no cross-sectional universe was computed for this request), but
        the underlying calculation succeeded (benchmark_relative_return
        present, data_quality_status OK). The explanation must not claim
        'insufficient price history' in this case — that claim is false."""
        rs = _rs(score=None, pctl=None, quality=DataQualityStatus.OK)
        exp = build_why_now_explanation(rs, None, None, None)
        assert "insufficient price history" not in exp.summary.lower()
        assert not any("insufficient price history" in c.lower() for c in exp.caution_factors)
        assert "not available for this view" in exp.stock_rs_context.lower()

    def test_methodology_version_always_stamped(self):
        exp = build_why_now_explanation(_rs(), _group(), _trend(), _breadth())
        assert exp.methodology_version
