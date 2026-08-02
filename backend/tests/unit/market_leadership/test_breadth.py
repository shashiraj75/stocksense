"""Unit tests for breadth.py (pure aggregation, no I/O)."""
import datetime

import pytest

from services.market_leadership.contracts import BreadthState, RsTrend
from services.market_leadership.breadth import (
    BreadthMemberFact, compute_breadth_components, classify_breadth_state, build_market_breadth,
)

AS_OF = datetime.date(2026, 7, 20)


def _member(sym, sessions=250, above20=True, above50=True, above200=True,
           new_high=False, new_low=False, trend=RsTrend.IMPROVING, ret21=2.0):
    return BreadthMemberFact(sym, sessions, above20, above50, above200, new_high, new_low, trend, ret21)


@pytest.mark.unit
class TestComputeBreadthComponents:
    def test_insufficient_when_no_members(self):
        stats = compute_breadth_components([], None)
        assert stats["insufficient"] is True

    def test_insufficient_when_coverage_below_threshold(self):
        members = [_member(f"S{i}", sessions=250) for i in range(3)] + \
                  [_member(f"T{i}", sessions=10) for i in range(20)]  # most under min sessions
        stats = compute_breadth_components(members, None)
        assert stats["insufficient"] is True

    def test_healthy_market_has_high_percentages(self):
        members = [_member(f"S{i}") for i in range(20)]
        stats = compute_breadth_components(members, 1.0)
        assert stats["pct_above_200dma"] == pytest.approx(1.0)
        assert stats["insufficient"] is False

    def test_coverage_and_numerator_denominator_disclosed(self):
        members = [_member(f"S{i}") for i in range(20)]
        stats = compute_breadth_components(members, None)
        assert stats["n200"] == 20 and stats["d200"] == 20

    def test_new_highs_and_lows_counted(self):
        members = [_member(f"S{i}", new_high=(i < 5)) for i in range(20)]
        stats = compute_breadth_components(members, None)
        assert stats["new_highs_63d"] == 5

    def test_divergence_flag_when_benchmark_and_median_disagree(self):
        members = [_member(f"S{i}", ret21=-3.0) for i in range(20)]  # median negative
        stats = compute_breadth_components(members, benchmark_return_21d=5.0)  # benchmark positive
        assert stats["divergence"] is True


@pytest.mark.unit
class TestClassifyBreadthState:
    def test_insufficient_data_state(self):
        stats = compute_breadth_components([], None)
        assert classify_breadth_state(stats) == BreadthState.INSUFFICIENT_DATA

    def test_healthy_state(self):
        members = [_member(f"S{i}") for i in range(20)]
        stats = compute_breadth_components(members, 1.0)
        assert classify_breadth_state(stats) == BreadthState.HEALTHY

    def test_washed_out_state(self):
        members = [_member(f"S{i}", above200=False, new_low=True, new_high=False, ret21=-5.0) for i in range(20)]
        stats = compute_breadth_components(members, None)
        assert classify_breadth_state(stats) == BreadthState.WASHED_OUT

    def test_deteriorating_state(self):
        members = [_member(f"S{i}", above50=False, above200=True, new_low=True, ret21=-1.0) for i in range(20)]
        stats = compute_breadth_components(members, None)
        state = classify_breadth_state(stats)
        assert state in (BreadthState.DETERIORATING, BreadthState.NARROWING)

    def test_state_precedence_is_deterministic_and_exclusive(self):
        """Property: the same stats always resolve to exactly one state."""
        members = [_member(f"S{i}") for i in range(20)]
        stats = compute_breadth_components(members, 1.0)
        s1 = classify_breadth_state(stats)
        s2 = classify_breadth_state(dict(stats))
        assert s1 == s2


@pytest.mark.unit
class TestBuildMarketBreadth:
    def test_contract_has_coverage_and_components(self):
        members = [_member(f"S{i}") for i in range(20)]
        contract = build_market_breadth("IN", "market", members, 1.0, AS_OF, AS_OF, "u1")
        assert contract.coverage > 0
        assert len(contract.components) == 3
        assert all(c.coverage is not None for c in contract.components)

    def test_low_coverage_never_silently_published_as_a_confident_state(self):
        members = [_member(f"S{i}", sessions=10) for i in range(20)]
        contract = build_market_breadth("US", "market", members, None, AS_OF, AS_OF, "u1")
        assert contract.state == BreadthState.INSUFFICIENT_DATA
        assert "LOW_COVERAGE" in contract.reason_codes

    def test_market_never_inferred_carries_through_verbatim(self):
        members = [_member(f"S{i}") for i in range(20)]
        contract = build_market_breadth("US", "market", members, 1.0, AS_OF, AS_OF, "u1")
        assert contract.market == "US"
