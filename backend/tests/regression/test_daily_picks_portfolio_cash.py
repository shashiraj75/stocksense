"""
DP-020 (DPD-005, DECIDED 2026-07-18) — end-to-end integration: the
published Daily Picks payload must surface the cash/unallocated fraction
left over when the optimizer's hard per-name cap prevents full investment,
via the new additive `payload["portfolio_cash_pct"]` field.

This exercises `_generate_picks_inner` through the REAL optimizer (not
mocked, unlike test_alpha_observations.py's shadow-integration fixture)
so the actual DP-020 fix is proven at the point it reaches the published
payload — with everything else (screening, scoring, ranking, persistence)
heavily mocked, no network/production-DB access.
"""

from unittest.mock import patch

import pandas as pd
import pytest

import services.daily_picks as dp


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


def _run(candidate_symbols, buy_count):
    """Full-pipeline run with `buy_count` BUY signals out of
    `len(candidate_symbols)` scored candidates per horizon, using the REAL
    optimizer (services.alpha_engine.optimizer.optimize is NOT mocked)."""

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
        payload, _persisted_at = dp._generate_picks_inner("US", job_id="job-cash-test")
    return payload


@pytest.mark.regression
class TestPortfolioCashPctField:
    def test_two_buy_picks_surfaces_twenty_percent_cash_per_horizon(self):
        payload = _run(["AAA", "BBB"], buy_count=2)
        assert "portfolio_cash_pct" in payload
        for horizon in ("short", "medium", "long"):
            picks = payload["picks"][horizon]
            assert len(picks) == 2
            for pick in picks:
                assert pick["portfolio_weight"] <= 0.40 + 1e-6
            total_weight = sum(p["portfolio_weight"] for p in picks)
            assert payload["portfolio_cash_pct"][horizon] == pytest.approx(0.20, abs=1e-3)
            # weights + cash must total 100%, within numeric tolerance
            assert total_weight + payload["portfolio_cash_pct"][horizon] == pytest.approx(1.0, abs=1e-3)

    def test_three_buy_picks_stays_fully_invested_zero_cash(self):
        payload = _run(["AAA", "BBB", "CCC"], buy_count=3)
        for horizon in ("short", "medium", "long"):
            picks = payload["picks"][horizon]
            assert len(picks) == 3
            total_weight = sum(p["portfolio_weight"] for p in picks)
            assert total_weight == pytest.approx(1.0, abs=1e-3)
            assert payload["portfolio_cash_pct"][horizon] == pytest.approx(0.0, abs=1e-3)

    def test_single_buy_pick_reports_fifty_percent_cash(self):
        payload = _run(["AAA"], buy_count=1)
        for horizon in ("short", "medium", "long"):
            picks = payload["picks"][horizon]
            assert len(picks) == 1
            assert picks[0]["portfolio_weight"] == 0.50
            assert payload["portfolio_cash_pct"][horizon] == pytest.approx(0.50, abs=1e-3)

    def test_no_buy_picks_leaves_horizon_absent_from_cash_dict(self):
        payload = _run(["AAA", "BBB"], buy_count=0)
        for horizon in ("short", "medium", "long"):
            assert payload["picks"][horizon] == []
        # No portfolio optimisation ran for any horizon (0 picks) — no cash
        # entry fabricated for a horizon that never had a portfolio.
        assert payload["portfolio_cash_pct"] == {}

    def test_published_pick_fields_otherwise_unaffected(self):
        # DP-020 must not touch any other field on the published pick dict.
        payload = _run(["AAA", "BBB"], buy_count=2)
        pick = payload["picks"]["short"][0]
        assert pick["signal"] == "BUY"
        assert pick["confidence"] == 90
        assert "portfolio_cash_pct" not in pick  # cash lives at payload level, not per-pick
