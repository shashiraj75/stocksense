"""Unit tests for group_leadership.py (pure aggregation, no I/O)."""
import pytest

from services.market_leadership.contracts import GroupState, LeadershipConcentration, RsTrend
from services.market_leadership.group_leadership import (
    MemberFact, aggregate_group, classify_group_state, classify_concentration,
    rank_groups, build_group_leadership, _capped_weights,
)


def _members(n_scored, pctl=80.0, trend=RsTrend.IMPROVING, n_unscored=0, market_cap=None):
    scored = [MemberFact(f"S{i}", pctl, trend, market_cap, True, False) for i in range(n_scored)]
    unscored = [MemberFact(f"U{i}", None, RsTrend.INSUFFICIENT_DATA, None, None, None) for i in range(n_unscored)]
    return scored + unscored


@pytest.mark.unit
class TestAggregateGroup:
    def test_insufficient_when_too_few_scored_members(self):
        stats = aggregate_group(_members(3))
        assert stats["insufficient"] is True

    def test_insufficient_when_coverage_too_low(self):
        stats = aggregate_group(_members(5, n_unscored=20))  # 5/25 = 20% coverage
        assert stats["insufficient"] is True

    def test_sufficient_group_has_all_fields(self):
        stats = aggregate_group(_members(10, pctl=75.0))
        assert stats["insufficient"] is False
        assert stats["median_member_rs"] == pytest.approx(75.0)
        assert stats["leadership_breadth"] == pytest.approx(1.0)  # all >= 60

    def test_leadership_breadth_reflects_share_above_threshold(self):
        members = [MemberFact(f"S{i}", 90.0 if i < 3 else 20.0, RsTrend.FLAT, None, None, None) for i in range(10)]
        stats = aggregate_group(members)
        assert stats["leadership_breadth"] == pytest.approx(0.3)

    def test_one_mega_cap_cannot_dominate_the_weighted_score(self):
        """A single huge-cap member with a mediocre score must not pull the
        weighted score away from a broad, strong median — the cap ceiling
        bounds any one member's influence."""
        strong_broad = [MemberFact(f"S{i}", 85.0, RsTrend.IMPROVING, 1e9, True, False) for i in range(9)]
        mega_weak = [MemberFact("MEGA", 20.0, RsTrend.WEAKENING, 1e13, True, False)]  # dwarfs the rest
        stats = aggregate_group(strong_broad + mega_weak)
        assert stats["weighted_member_rs"] > 60.0  # capped weight prevents domination


@pytest.mark.unit
class TestCappedWeights:
    def test_no_member_exceeds_the_cap(self):
        members = [MemberFact("BIG", 80.0, RsTrend.FLAT, 1e12, None, None)] + [
            MemberFact(f"S{i}", 80.0, RsTrend.FLAT, 1e6, None, None) for i in range(9)
        ]
        weights = _capped_weights(members)
        assert weights["BIG"] <= 0.20 + 1e-9

    def test_weights_sum_to_one(self):
        members = [MemberFact(f"S{i}", 80.0, RsTrend.FLAT, float(i + 1) * 1e6, None, None) for i in range(10)]
        weights = _capped_weights(members)
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)

    def test_falls_back_to_equal_weight_with_no_cap_data(self):
        members = [MemberFact(f"S{i}", 80.0, RsTrend.FLAT, None, None, None) for i in range(5)]
        weights = _capped_weights(members)
        assert all(w == pytest.approx(0.2) for w in weights.values())

    def test_extreme_concentration_never_violates_the_cap_even_after_redistribution(self):
        """Regression: fresh pre-merge adversarial review found the previous
        iterative algorithm could push an ALREADY-capped member back over
        the ceiling in a later redistribution round (it re-classified any
        member sitting exactly AT the cap as 'under', eligible to receive
        further excess), failing to converge within the fixed iteration
        budget under sufficiently extreme concentration. This exact
        7-member, ~1e9x concentration-ratio scenario reproduced a real
        violation (MEGA and C both ended up at 0.2023, over the 0.20 cap)
        before the fix — every member must be <= the cap after the fix,
        with no member permanently re-inflated once locked."""
        members = [
            MemberFact("MEGA", 90.0, RsTrend.FLAT, 1e15, True, False),
            MemberFact("A", 50.0, RsTrend.FLAT, 1e9, True, False),
            MemberFact("B", 60.0, RsTrend.FLAT, 5e8, True, False),
            MemberFact("C", 40.0, RsTrend.FLAT, 2e8, True, False),
            MemberFact("D", 70.0, RsTrend.FLAT, 1e8, True, False),
            MemberFact("E", 30.0, RsTrend.FLAT, 5e7, True, False),
            MemberFact("F", 55.0, RsTrend.FLAT, 1e6, True, False),
        ]
        weights = _capped_weights(members)
        assert all(w <= 0.20 + 1e-6 for w in weights.values()), weights
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)

    def test_even_more_extreme_ten_member_concentration_still_converges(self):
        members = [MemberFact("ULTRA", 90.0, RsTrend.FLAT, 1e18, True, False)] + [
            MemberFact(f"S{i}", 50.0, RsTrend.FLAT, 1e6 * (i + 1), True, False) for i in range(9)
        ]
        weights = _capped_weights(members)
        assert all(w <= 0.20 + 1e-6 for w in weights.values())
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)

    def test_mathematically_infeasible_cap_falls_back_to_equal_weight(self):
        """3 members with a 20% per-member cap cannot both respect every
        cap and sum to 1.0 (3 * 0.20 = 0.60 < 1.0) — must gracefully fall
        back to equal weight rather than silently violating the cap or
        looping without converging."""
        members = [
            MemberFact("X", 50.0, RsTrend.FLAT, 1e12, True, False),
            MemberFact("Y", 50.0, RsTrend.FLAT, 1e6, True, False),
            MemberFact("Z", 50.0, RsTrend.FLAT, 1e6, True, False),
        ]
        weights = _capped_weights(members)
        assert all(w == pytest.approx(1 / 3) for w in weights.values())
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
class TestClassifyGroupState:
    def test_insufficient_data_state(self):
        stats = aggregate_group(_members(2))
        assert classify_group_state(stats, None, "UP") == GroupState.INSUFFICIENT_DATA

    def test_leading_requires_high_score_and_breadth(self):
        stats = aggregate_group(_members(10, pctl=80.0))
        assert classify_group_state(stats, 75.0, "UP") == GroupState.LEADING

    def test_lagging_below_threshold(self):
        stats = aggregate_group(_members(10, pctl=20.0, trend=RsTrend.WEAKENING))
        assert classify_group_state(stats, 20.0, "DOWN") == GroupState.LAGGING


@pytest.mark.unit
class TestClassifyConcentration:
    def test_broad_when_high_breadth_and_small_weight_gap(self):
        stats = aggregate_group(_members(10, pctl=70.0))
        assert classify_concentration(stats) == LeadershipConcentration.BROAD

    def test_insufficient_data_propagates(self):
        stats = aggregate_group(_members(2))
        assert classify_concentration(stats) == LeadershipConcentration.INSUFFICIENT_DATA


@pytest.mark.unit
class TestRankGroups:
    def test_highest_score_ranks_first(self):
        ranks = rank_groups({"A": 50.0, "B": 90.0, "C": None})
        assert ranks["B"] == 1
        assert ranks["C"] is None

    def test_identical_inputs_produce_identical_outputs(self):
        scores = {"A": 10.0, "B": 20.0, "C": 30.0}
        assert rank_groups(scores) == rank_groups(dict(scores))


@pytest.mark.unit
class TestBuildGroupLeadership:
    def test_contract_carries_versions_and_pit_disclosure(self):
        import datetime
        contract = build_group_leadership(
            "IN", "IT", _members(10, pctl=75.0), datetime.date(2026, 7, 20),
            "u1", 75.0, 2, None, "UP",
        )
        assert contract.classification_pit_safe is False
        assert contract.methodology_version
        assert contract.market == "IN"
        assert contract.sector_name == "IT"
        assert contract.industry_name == "IT"  # v1: single classification level
