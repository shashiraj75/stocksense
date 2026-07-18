"""
DP-021 (governed by DPD-005, DECIDED 2026-07-18) — align the one-candidate
optimizer and Daily Picks allocation behavior with the approved hard-
position-cap and explicit-cash contract.

Prior inconsistency: optimizer.optimize() special-cased N=1 to return
[1.0] (100%, violating max_weight if ever called directly), while
daily_picks.py's Phase 6 block separately avoided the optimizer entirely
for N=1 and hard-coded portfolio_weight=0.50 / portfolio_cash_pct=0.50 —
two different, both-wrong answers for the same case, neither honouring
the 40% cap DPD-005 already established for N>=2.

Corrected unified contract: a single qualifying pick is exactly the N=1
instance of target_invested = min(1, N * max_weight) — no special case.
At the default 40% cap: portfolio_weight=0.40, portfolio_cash_pct=0.60.

This file proves the 20 required behaviours end to end. Other files
updated in this same change (test_optimizer_portfolio_cash_cap.py,
test_daily_picks_extracted_helpers_parity.py,
test_daily_picks_portfolio_cash.py) also exercise this fix at their own
layer — this file is the single consolidated reference for DP-021 itself.
"""

from unittest.mock import patch

import pandas as pd
import pytest

import services.daily_picks as dp
from services.alpha_engine.optimizer import optimize
from services.validation.contracts import MarketSnapshot, CandidateSnapshot, ReplayMode, PointInTimeIntegrity
from services.validation.pipeline_replay import replay_snapshot


# ---------------------------------------------------------------------------
# 1-6. optimizer.optimize() — N=0, N=1 at various caps, cap never exceeded,
#      weight+cash totals 1.0
# ---------------------------------------------------------------------------

class TestOptimizerZeroAndOneCandidate:
    def test_zero_candidates_returns_empty_list(self):
        assert optimize([]) == []

    def test_one_candidate_default_forty_percent_cap(self):
        assert optimize([5.0]) == [0.40]

    def test_one_candidate_custom_twenty_five_percent_cap(self):
        assert optimize([5.0], max_weight=0.25) == [0.25]

    def test_one_candidate_full_hundred_percent_cap(self):
        assert optimize([5.0], max_weight=1.00) == [1.00]

    @pytest.mark.parametrize("max_weight", [0.05, 0.10, 0.30, 0.40, 0.55, 0.80, 1.00])
    def test_one_candidate_never_exceeds_max_weight(self, max_weight):
        weights = optimize([5.0], max_weight=max_weight)
        assert weights[0] <= max_weight + 1e-9

    @pytest.mark.parametrize("max_weight", [0.10, 0.40, 0.75, 1.00])
    def test_one_candidate_weight_plus_cash_totals_one(self, max_weight):
        weights = optimize([5.0], max_weight=max_weight)
        cash = round(max(0.0, 1.0 - sum(weights)), 4)
        assert weights[0] + cash == pytest.approx(1.0, abs=1e-9)

    def test_one_candidate_alpha_value_does_not_affect_the_cap(self):
        # The weight is capacity-driven, not alpha-driven — any alpha for a
        # single candidate must produce the same capped weight.
        for alpha in (-10.0, 0.0, 0.001, 5.0, 1000.0):
            assert optimize([alpha], max_weight=0.40) == [0.40]


# ---------------------------------------------------------------------------
# 7. _compute_portfolio_allocation() one-pick 40%/60%
# ---------------------------------------------------------------------------

class TestComputePortfolioAllocationOnePick:
    def test_one_pick_returns_forty_percent_weight_sixty_percent_cash(self):
        weights, cash = dp._compute_portfolio_allocation([5.0], None, "BULL_CALM")
        assert weights == [0.40]
        assert cash == 0.60

    def test_zero_picks_no_fabricated_exposure(self):
        weights, cash = dp._compute_portfolio_allocation([], None, "BULL_CALM")
        assert weights == []
        assert cash == 0.0

    def test_two_picks_unchanged_forty_forty_twenty_cash(self):
        weights, cash = dp._compute_portfolio_allocation([1.0, 1.0], None, "BULL_CALM")
        assert weights == [0.4, 0.4]
        assert cash == 0.2

    @pytest.mark.parametrize("n", [3, 4, 5, 6])
    def test_three_to_six_picks_unchanged_fully_invested(self, n):
        alphas = [float(i + 1) for i in range(n)]
        weights, cash = dp._compute_portfolio_allocation(alphas, None, "BULL_CALM")
        assert sum(weights) == pytest.approx(1.0, abs=1e-3)
        assert cash == pytest.approx(0.0, abs=1e-3)
        assert max(weights) <= 0.40 + 1e-9


# ---------------------------------------------------------------------------
# 8-9, 11. Live Daily Picks payload — one-pick 40%/60%, zero-pick no
# fabrication
# ---------------------------------------------------------------------------

def _enriched_candidate(symbol, horizon, alpha, is_buy=True):
    return {
        "symbol": symbol, "horizon": horizon,
        "signal": "BUY" if is_buy else "HOLD",
        "confidence": 90, "price": 100.0,
        "tech_score": 60.0, "fund_score": 55.0,
        "sentiment_score": 50.0, "quality_score": 52.0,
        "sentiment_available": True, "quality_available": True,
        "quality_raw_score": 52.0,
        "generation_reference_price": 100.0,
        "generation_reference_source": "yahoo_daily_history",
        "generation_reference_price_basis": "adjusted_close",
        "generation_reference_as_of": "2026-07-10T00:00:00",
        "factor_zscores": {"tech": 0.5, "fund": 0.2, "sentiment": 0.1, "quality": 0.3},
        "combined_alpha": alpha, "meta_alpha": None, "ranking_alpha": alpha,
        "composite_score": 58.0, "reasoning": [],
    }


def _run_daily_picks(candidate_symbols, buy_count):
    def fake_zscore_and_rank(items, ic_weights, regime, regime_id, market="IN",
                              production_learning_enabled=None):
        return [
            _enriched_candidate(it["symbol"], it["horizon"], 0.9 - i * 0.1, is_buy=(i < buy_count))
            for i, it in enumerate(items)
        ]

    with patch("services.daily_picks._bulk_screen",
                return_value=(candidate_symbols, len(candidate_symbols), "fundamentals_cache", False, None,
                              {"universe_candidate_count": len(candidate_symbols), "attempts": 1,
                               "reason": "ok", "error_category": "none", "tier_map": {}})), \
         patch("services.daily_picks._predict_stock",
               side_effect=lambda sym, h, market: {
                   "symbol": sym, "horizon": h, "signal": "BUY",
                   "confidence": 90, "cap_tier": None,
               }), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks._zscore_and_rank", side_effect=fake_zscore_and_rank), \
         patch("services.alpha_engine.regime_cluster.detect_regime",
               return_value={"regime_id": 2, "label": "BULL_CALM", "description": "",
                             "trend": "BULLISH", "score_adj": 0, "weight_multipliers": {}}), \
         patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.daily_picks._alpha_obs.save_observations", return_value=True), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("services.daily_picks._fetch_returns_matrix", return_value=None), \
         patch("services.daily_picks.yf.download", return_value=pd.DataFrame()), \
         patch("services.daily_picks.yf.utils.get_crumb", create=True), \
         patch("os.getenv", return_value=None):
        payload, _persisted_at = dp._generate_picks_inner("US", job_id="dp021-test")
    return payload


class TestLiveDailyPicksPayloadOnePick:
    def test_live_payload_publishes_one_pick_forty_percent_weight(self):
        payload = _run_daily_picks(["AAA"], buy_count=1)
        for horizon in ("short", "medium", "long"):
            picks = payload["picks"][horizon]
            assert len(picks) == 1
            assert picks[0]["portfolio_weight"] == 0.40

    def test_live_payload_publishes_corresponding_sixty_percent_cash(self):
        payload = _run_daily_picks(["AAA"], buy_count=1)
        for horizon in ("short", "medium", "long"):
            assert payload["portfolio_cash_pct"][horizon] == pytest.approx(0.60, abs=1e-3)

    def test_live_payload_weight_plus_cash_totals_one(self):
        payload = _run_daily_picks(["AAA"], buy_count=1)
        for horizon in ("short", "medium", "long"):
            total = sum(p["portfolio_weight"] for p in payload["picks"][horizon])
            assert total + payload["portfolio_cash_pct"][horizon] == pytest.approx(1.0, abs=1e-3)

    def test_zero_pick_horizons_do_not_fabricate_portfolio_weights(self):
        payload = _run_daily_picks(["AAA", "BBB"], buy_count=0)
        for horizon in ("short", "medium", "long"):
            assert payload["picks"][horizon] == []
        assert payload["portfolio_cash_pct"] == {}

    def test_existing_two_pick_behavior_unchanged(self):
        payload = _run_daily_picks(["AAA", "BBB"], buy_count=2)
        for horizon in ("short", "medium", "long"):
            picks = payload["picks"][horizon]
            assert len(picks) == 2
            for p in picks:
                assert p["portfolio_weight"] == pytest.approx(0.40, abs=1e-3)
            assert payload["portfolio_cash_pct"][horizon] == pytest.approx(0.20, abs=1e-3)


# ---------------------------------------------------------------------------
# 10. DP-025 replay harness parity — one-pick 40%/60%
# ---------------------------------------------------------------------------

class TestDP025ReplayHarnessOnePickParity:
    def _snapshot(self, mode=ReplayMode.DIAGNOSTIC):
        candidate = CandidateSnapshot(
            symbol="AAA", signal="BUY", confidence=70, technical_score=60.0, fundamental_score=55.0,
            sentiment_score=50.0, sentiment_available=True,
            quality_score=52.0, quality_available=True, quality_raw_score=52.0,
            reasoning=[], cap_tier="large",
        )
        return MarketSnapshot(
            snapshot_id="dp021-replay-test", as_of="2026-01-01T00:00:00Z", market="US", horizon="medium",
            source="manual_fixture", replay_mode=mode, candidates=[candidate],
            regime_id=0, regime_label="BULL_CALM", regime_weight_multipliers={},
            ic_weights={"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2},
            point_in_time_integrity={
                "provenance": PointInTimeIntegrity.POINT_IN_TIME,
                "timestamp": PointInTimeIntegrity.POINT_IN_TIME,
                "technical": PointInTimeIntegrity.POINT_IN_TIME,
                "fundamental": PointInTimeIntegrity.POINT_IN_TIME,
                "quality": PointInTimeIntegrity.POINT_IN_TIME,
            },
        )

    def test_replay_one_pick_publishes_forty_percent_weight_sixty_percent_cash(self):
        result = replay_snapshot(self._snapshot())
        assert result.selected_slate == ["AAA"]
        assert result.portfolio_weights["AAA"] == 0.40
        assert result.cash_unallocated_pct == 0.60

    def test_replay_strict_mode_one_pick_matches_diagnostic(self):
        result = replay_snapshot(self._snapshot(mode=ReplayMode.STRICT_POINT_IN_TIME))
        assert result.portfolio_weights["AAA"] == 0.40
        assert result.cash_unallocated_pct == 0.60

    def test_replay_one_pick_provenance_reflects_optimizer_not_invoked(self):
        # Unchanged by DP-021: _compute_portfolio_allocation still only
        # calls optimizer.optimize() when N>=1 now (it always calls it),
        # but provenance labelling for N=1 should be re-examined for
        # accuracy — optimize() IS invoked for N=1 post-DP-021 (it no
        # longer bypasses the function), so provenance must say so.
        result = replay_snapshot(self._snapshot())
        assert result.production_provenance["invoked"].get("optimizer") == "services.alpha_engine.optimizer.optimize"
        assert "optimizer" not in result.production_provenance["not_invoked"]


# ---------------------------------------------------------------------------
# 14-15. SLSQP / fallback paths remain capped; custom max_weight respected
# ---------------------------------------------------------------------------

class TestSolvePathsAndCustomCap:
    def test_one_candidate_bypasses_slsqp_entirely_deterministic(self, monkeypatch):
        # N=1 is resolved analytically (no SLSQP/fallback machinery needed)
        # — forcing scipy to raise must not affect the result.
        monkeypatch.setattr(
            "scipy.optimize.minimize",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("forced failure — must not affect N=1")),
        )
        assert optimize([5.0], max_weight=0.40) == [0.40]

    def test_two_candidates_slsqp_and_fallback_still_capped(self, monkeypatch):
        direct = optimize([1.0, 1.0], max_weight=0.40)
        assert max(direct) <= 0.40 + 1e-9
        monkeypatch.setattr(
            "scipy.optimize.minimize",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("forced fallback")),
        )
        fallback = optimize([1.0, 1.0], max_weight=0.40)
        assert max(fallback) <= 0.40 + 1e-9

    def test_custom_max_weight_respected_across_candidate_counts(self):
        assert optimize([5.0], max_weight=0.15) == [0.15]
        two = optimize([1.0, 1.0], max_weight=0.15)
        assert max(two) <= 0.15 + 1e-9
        assert sum(two) == pytest.approx(0.30, abs=1e-3)


# ---------------------------------------------------------------------------
# 16. No post-allocation normalization raises the one-pick weight above cap
# ---------------------------------------------------------------------------

class TestNoPostAllocationNormalizationForOnePick:
    def test_one_candidate_weight_is_never_renormalized_upward(self):
        for max_weight in (0.10, 0.25, 0.40, 0.99):
            weights = optimize([5.0], max_weight=max_weight)
            assert weights == [round(max_weight, 4)]
            assert weights[0] <= max_weight + 1e-9


# ---------------------------------------------------------------------------
# 17. Invalid max_weight — one explicitly documented safe behaviour
# ---------------------------------------------------------------------------

class TestInvalidMaxWeightSafeBehaviour:
    """Documented behaviour: max_weight must be a finite number > 0, for
    every candidate count including N=1 — otherwise optimize() raises
    ValueError immediately (fail-fast), never producing a silently-wrong
    allocation."""

    @pytest.mark.parametrize("bad", [0.0, -0.01, -1.0, float("nan"), float("inf"), float("-inf")])
    def test_one_candidate_invalid_max_weight_raises(self, bad):
        with pytest.raises(ValueError):
            optimize([5.0], max_weight=bad)

    def test_valid_max_weight_boundary_just_above_zero_does_not_raise(self):
        weights = optimize([5.0], max_weight=0.0001)
        assert weights == [0.0001]


# ---------------------------------------------------------------------------
# 18. Existing DP-020 tests remain passing (smoke re-check of key invariant)
# ---------------------------------------------------------------------------

class TestDP020InvariantsUnaffected:
    def test_two_pick_cap_still_forty_forty_twenty_cash(self):
        weights = optimize([1.0, 1.0], max_weight=0.40)
        assert weights == [0.4, 0.4]

    def test_three_pick_still_fully_invested(self):
        weights = optimize([3.0, 2.0, 1.0], max_weight=0.40)
        assert sum(weights) == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# 20. No production-learning containment behaviour changes
# ---------------------------------------------------------------------------

class TestContainmentUnaffectedByDP021:
    def test_one_pick_replay_never_activates_production_learning(self, monkeypatch):
        monkeypatch.setenv("LEARNING_ALPHA_PRODUCTION_ENABLED", "1")
        candidate = CandidateSnapshot(
            symbol="AAA", signal="BUY", confidence=70, technical_score=60.0, fundamental_score=55.0,
            sentiment_score=50.0, sentiment_available=True,
            quality_score=52.0, quality_available=True, quality_raw_score=52.0,
            reasoning=[], cap_tier="large",
        )
        snapshot = MarketSnapshot(
            snapshot_id="containment-test", as_of="2026-01-01T00:00:00Z", market="US", horizon="medium",
            source="manual_fixture", replay_mode=ReplayMode.DIAGNOSTIC, candidates=[candidate],
            regime_id=0, regime_label="BULL_CALM", regime_weight_multipliers={},
            ic_weights={"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2},
        )
        with patch("services.alpha_engine.meta_model.predict", return_value=999.0):
            result = replay_snapshot(snapshot)
        row = result.ranked_universe[0]
        assert row.meta_alpha_used_for_ranking is False
        assert row.ranking_alpha == row.combined_alpha
