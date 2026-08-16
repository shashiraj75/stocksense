"""
feature/daily-picks-conviction-gated-publication — unit tests for
_apply_conviction_publication_gate() and the DAILY_PICKS_PUBLICATION
threshold registry (services/thresholds.py).

Scope: this is a PUBLICATION filter applied to an already-eligible,
already-ranked BUY slate (what production calls `top_buy`). It must never
re-rank, re-score, or manufacture candidates — only decide, in the given
order, which ones are published (0-3 per horizon, gated on
Model Conviction == the existing "confidence" field >= 85.0).
"""

import math

import services.daily_picks as dp
from services.thresholds import DAILY_PICKS_PUBLICATION


def _cand(symbol, confidence):
    return {"symbol": symbol, "confidence": confidence, "ranking_alpha": 1.0}


class TestThresholdRegistry:
    def test_threshold_is_85(self):
        assert DAILY_PICKS_PUBLICATION.MIN_CONVICTION_TO_PUBLISH == 85.0

    def test_max_published_per_horizon_is_3(self):
        assert DAILY_PICKS_PUBLICATION.MAX_PUBLISHED_PER_HORIZON == 3


class TestConvictionGateCounts:
    def test_zero_qualifying_candidates(self):
        candidates = [_cand("AAA", 50), _cand("BBB", 60), _cand("CCC", 84.9)]
        published, meta = dp._apply_conviction_publication_gate(candidates)
        assert published == []
        assert meta["n_published"] == 0
        assert meta["n_conviction_qualified"] == 0

    def test_one_qualifying_candidate(self):
        candidates = [_cand("AAA", 90), _cand("BBB", 60)]
        published, meta = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in published] == ["AAA"]
        assert meta["n_published"] == 1

    def test_two_qualifying_candidates(self):
        candidates = [_cand("AAA", 90), _cand("BBB", 88), _cand("CCC", 50)]
        published, meta = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in published] == ["AAA", "BBB"]
        assert meta["n_published"] == 2

    def test_three_qualifying_candidates(self):
        candidates = [_cand("AAA", 99), _cand("BBB", 90), _cand("CCC", 85)]
        published, meta = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in published] == ["AAA", "BBB", "CCC"]
        assert meta["n_published"] == 3

    def test_more_than_three_qualifying_candidates_capped_at_three(self):
        candidates = [_cand(s, c) for s, c in
                      [("AAA", 99), ("BBB", 95), ("CCC", 90), ("DDD", 87), ("EEE", 85)]]
        published, meta = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in published] == ["AAA", "BBB", "CCC"]
        assert meta["n_published"] == 3
        assert meta["n_conviction_qualified"] == 5
        # positions 4-6 are excluded from publication but not manufactured
        # or deleted from the caller's own `candidates` list.
        assert candidates[3]["symbol"] == "DDD"
        assert candidates[4]["symbol"] == "EEE"

    def test_no_manufactured_candidates_from_empty_input(self):
        published, meta = dp._apply_conviction_publication_gate([])
        assert published == []
        assert meta["n_published"] == 0
        assert meta["n_conviction_qualified"] == 0


class TestConvictionThresholdBoundary:
    def test_84_99_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", 84.99)])
        assert published == []

    def test_85_00_included(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", 85.0)])
        assert [p["symbol"] for p in published] == ["AAA"]

    def test_85_01_included(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", 85.01)])
        assert [p["symbol"] for p in published] == ["AAA"]


class TestFailClosedOnInvalidConviction:
    def test_missing_confidence_excluded(self):
        c = {"symbol": "AAA", "ranking_alpha": 1.0}
        published, meta = dp._apply_conviction_publication_gate([c])
        assert published == []
        assert meta["n_conviction_qualified"] == 0

    def test_none_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", None)])
        assert published == []

    def test_non_numeric_string_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", "high")])
        assert published == []

    def test_bool_confidence_excluded(self):
        # bool is technically an int subclass in Python — must be explicitly
        # rejected so True (== 1) can never masquerade as a valid score.
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", True)])
        assert published == []

    def test_nan_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", math.nan)])
        assert published == []

    def test_positive_infinity_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", math.inf)])
        assert published == []

    def test_negative_infinity_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", -math.inf)])
        assert published == []

    def test_negative_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", -5)])
        assert published == []

    def test_over_100_confidence_excluded(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", 150)])
        assert published == []

    def test_exactly_100_included(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", 100)])
        assert [p["symbol"] for p in published] == ["AAA"]

    def test_exactly_zero_excluded_below_threshold(self):
        published, _ = dp._apply_conviction_publication_gate([_cand("AAA", 0)])
        assert published == []

    def test_invalid_candidate_does_not_block_valid_ones_after_it(self):
        candidates = [_cand("BAD", math.nan), _cand("GOOD", 90)]
        published, meta = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in published] == ["GOOD"]
        assert meta["n_conviction_qualified"] == 1


class TestRankingOrderPreserved:
    def test_order_not_resorted_by_confidence(self):
        # Deliberately NOT confidence-descending — the gate must preserve
        # whatever order the caller's existing ranking already produced,
        # not re-sort by conviction.
        candidates = [_cand("LOW_RANK_HIGH_CONF", 99), _cand("HIGH_RANK_LOWER_CONF", 90)]
        published, _ = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in published] == ["LOW_RANK_HIGH_CONF", "HIGH_RANK_LOWER_CONF"]


class TestMarketAndHorizonIndependence:
    def test_same_symbol_across_independent_calls(self):
        # India and US (and each horizon) call this function independently
        # per (market, horizon) slate in production — proving the function
        # itself has no cross-call state confirms that independence.
        in_short = dp._apply_conviction_publication_gate([_cand("TCS", 90)])
        us_short = dp._apply_conviction_publication_gate([_cand("TCS", 40)])
        assert len(in_short[0]) == 1
        assert len(us_short[0]) == 0

    def test_repeated_calls_are_pure_no_shared_state(self):
        candidates = [_cand("AAA", 90)]
        first, _ = dp._apply_conviction_publication_gate(candidates)
        second, _ = dp._apply_conviction_publication_gate(candidates)
        assert [p["symbol"] for p in first] == [p["symbol"] for p in second] == ["AAA"]
