"""
DP-009 / DP-010 — genuine zero factor scores must survive ranking.

Both defects had the same root cause: a Python `x or 50` truthiness
fallback, which treats 0/0.0 as falsy exactly like None/missing and
silently substitutes a fabricated neutral 50.

  DP-009: `daily_picks.py` `_predict_stock()` — `"quality_score":
  qf.get("score") or 50` converted a genuine quality score of 0 into 50
  before the value ever reached cross-sectional ranking.

  DP-010: `daily_picks.py` `_zscore_and_rank()` — `raw = float(raw_val or
  50)` ran *after* an explicit `raw_val is None` branch had already handled
  missing evidence, so this second fallback only ever fired on a genuine
  0/0.0 for tech/fund/quality, again substituting a fabricated 50.

These tests prove, without any network/production-DB access, that:
  1-3. _predict_stock() preserves a genuine 0 / 0.0 quality score, and a
       truly missing quality score still uses the intended neutral 50.
  4-6. _zscore_and_rank() treats technical/fundamental/quality scores of
       exactly 0 as real numeric evidence, not missing evidence.
  7.   A missing sentiment value still receives z = 0.0 (unchanged).
  8.   Zero values participate correctly in the cross-sectional mean/std
       used to compute z-scores (not replaced before or after the stats
       pass).
  9.   Production-learning containment is unaffected: the meta-model
       remains shadow-computed and ranking_alpha stays on the contained
       (combined_alpha) path unless the flag is explicitly enabled — which
       these tests never do.
  10.  Existing non-zero ranking behaviour is bit-for-bit unchanged.
"""

from unittest.mock import patch

import pytest

import services.daily_picks as dp


# ---------------------------------------------------------------------------
# DP-009 — _predict_stock() quality_score preservation
# ---------------------------------------------------------------------------

def _predict_stock_with_quality(qf_score):
    """Drive the real _predict_stock() through a mocked PredictionEngine.predict
    so the fix is proven at its actual source, not at a helper/builder level.
    No network access — PredictionEngine.predict is replaced outright."""
    quality_factors = {"score": qf_score} if qf_score is not None else {}

    async def fake_predict(self, symbol, market, horizon):
        return {
            "signal": "BUY", "confidence": 80, "current_price": 100.0,
            "reasoning": [], "trade_levels": {}, "quality_factors": quality_factors,
            "technical": {"score": 60}, "fundamental_score": {"score": 55},
            "sentiment_score": {"score": 50, "data_available": True},
            "price_reference": {
                "price": 100.0, "source": "yahoo_daily_history",
                "price_basis": "adjusted_close", "as_of": "2026-07-10T00:00:00",
            },
        }

    with patch.object(dp.PredictionEngine, "predict", fake_predict):
        return dp._predict_stock("ZEROQ", "short", "US")


class TestPredictStockPreservesGenuineZeroQualityScore:
    def test_integer_zero_quality_score_is_preserved(self):
        cand = _predict_stock_with_quality(0)
        assert cand["quality_score"] == 0
        assert cand["quality_score"] is not None
        assert cand["quality_available"] is True

    def test_float_zero_quality_score_is_preserved(self):
        cand = _predict_stock_with_quality(0.0)
        assert cand["quality_score"] == 0.0
        assert cand["quality_score"] is not None
        assert cand["quality_available"] is True

    def test_missing_quality_score_still_uses_neutral_fallback(self):
        # quality_factors == {} — genuinely absent, not a real 0.
        cand = _predict_stock_with_quality(None)
        assert cand["quality_score"] == 50
        assert cand["quality_available"] is False

    def test_nonzero_quality_score_unaffected(self):
        cand = _predict_stock_with_quality(37)
        assert cand["quality_score"] == 37


# ---------------------------------------------------------------------------
# DP-010 — _zscore_and_rank() zero-preservation across all four factors
# ---------------------------------------------------------------------------

IC_WEIGHTS = {"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}
REGIME = {"label": "BULL_CALM"}


def _candidate(symbol, tech=50.0, fund=50.0, sentiment=50.0, quality=50.0, horizon="short"):
    return {
        "symbol": symbol,
        "horizon": horizon,
        "signal": "BUY",
        "confidence": 70,
        "tech_score": tech,
        "fund_score": fund,
        "sentiment_score": sentiment,
        "quality_score": quality,
    }


def _run_zscore_and_rank(items, **kwargs):
    kwargs.setdefault("production_learning_enabled", False)
    # meta_model.predict is patched to a fixed shadow value in every test
    # below so shadow computation is provably still happening (DP-010 must
    # not touch containment) without depending on a trained model artifact
    # existing on disk.
    with patch("services.alpha_engine.meta_model.predict", return_value=-42.0) as mock_predict:
        result = dp._zscore_and_rank(items, IC_WEIGHTS, REGIME, regime_id=0, market="US", **kwargs)
    return result, mock_predict


class TestZscoreAndRankPreservesGenuineZeroFactorScores:
    def test_technical_score_zero_is_treated_as_numeric_zero_not_missing(self):
        items = [_candidate("AAA", tech=0.0), _candidate("BBB", tech=20.0), _candidate("CCC", tech=40.0)]
        enriched, _ = _run_zscore_and_rank(items)
        row_a = next(r for r in enriched if r["symbol"] == "AAA")
        # Cross-sectional mean of [0, 20, 40] is 20 — the tech=20 candidate
        # (BBB) should therefore land almost exactly at z=0, which is only
        # true if the 0 genuinely participated as 0, not as a fabricated 50.
        row_b = next(r for r in enriched if r["symbol"] == "BBB")
        assert row_b["factor_zscores"]["tech"] == pytest.approx(0.0, abs=1e-6)
        # AAA's own z-score must be negative (below-mean evidence), not the
        # 0.0-ish value it would get if its own raw value had been silently
        # replaced with 50 (which is *above* the true mean of 20).
        assert row_a["factor_zscores"]["tech"] < 0

    def test_fundamental_score_zero_is_treated_as_numeric_zero_not_missing(self):
        items = [_candidate("AAA", fund=0.0), _candidate("BBB", fund=20.0), _candidate("CCC", fund=40.0)]
        enriched, _ = _run_zscore_and_rank(items)
        row_b = next(r for r in enriched if r["symbol"] == "BBB")
        row_a = next(r for r in enriched if r["symbol"] == "AAA")
        assert row_b["factor_zscores"]["fund"] == pytest.approx(0.0, abs=1e-6)
        assert row_a["factor_zscores"]["fund"] < 0

    def test_quality_score_zero_is_treated_as_numeric_zero_not_missing(self):
        items = [_candidate("AAA", quality=0.0), _candidate("BBB", quality=20.0), _candidate("CCC", quality=40.0)]
        enriched, _ = _run_zscore_and_rank(items)
        row_b = next(r for r in enriched if r["symbol"] == "BBB")
        row_a = next(r for r in enriched if r["symbol"] == "AAA")
        assert row_b["factor_zscores"]["quality"] == pytest.approx(0.0, abs=1e-6)
        assert row_a["factor_zscores"]["quality"] < 0

    def test_zero_score_combined_alpha_differs_from_pre_fix_fabricated_value(self):
        # Regression guard against reintroducing `raw_val or 50`: with the
        # bug present, a tech=0 candidate's own z-score would collapse to
        # (50 - 20) / sigma = a *positive* number (since 50 > mean of 20),
        # inverting the sign of its true, below-mean evidence. Assert the
        # sign is correct instead of merely non-fifty.
        items = [_candidate("AAA", tech=0.0), _candidate("BBB", tech=20.0), _candidate("CCC", tech=40.0)]
        enriched, _ = _run_zscore_and_rank(items)
        row_a = next(r for r in enriched if r["symbol"] == "AAA")
        # production rounds z-scores to 3 decimals (round(z, 3))
        assert row_a["factor_zscores"]["tech"] == pytest.approx(-1.225, abs=1e-3)


class TestZscoreAndRankMissingSentimentUnaffected:
    def test_missing_sentiment_still_receives_neutral_zero_contribution(self):
        items = [_candidate("AAA", sentiment=None), _candidate("BBB", sentiment=60.0)]
        enriched, _ = _run_zscore_and_rank(items)
        row_a = next(r for r in enriched if r["symbol"] == "AAA")
        assert row_a["factor_zscores"]["sentiment"] == 0.0


class TestZscoreAndRankStatisticsIncludeGenuineZero:
    def test_mean_and_std_are_computed_from_the_true_zero_not_a_substituted_fifty(self):
        # tech = [0, 20, 40] -> true mean 20, population std (ddof=0)
        # sqrt(((0-20)^2 + 0 + (20)^2)/3) = sqrt(800/3) = 16.32993...
        # If DP-010 had also corrupted the stats pass (it never did — the
        # stats loop already read raw items directly — but this guards
        # against a future regression there too), the mean would shift to
        # (50+20+40)/3 = 36.67 and BBB's z would no longer be ~0.
        items = [_candidate("AAA", tech=0.0), _candidate("BBB", tech=20.0), _candidate("CCC", tech=40.0)]
        enriched, _ = _run_zscore_and_rank(items)
        row_b = next(r for r in enriched if r["symbol"] == "BBB")
        row_c = next(r for r in enriched if r["symbol"] == "CCC")
        assert row_b["factor_zscores"]["tech"] == pytest.approx(0.0, abs=1e-6)
        # production rounds z-scores to 3 decimals (round(z, 3))
        assert row_c["factor_zscores"]["tech"] == pytest.approx(1.225, abs=1e-3)


class TestZscoreAndRankContainmentUnaffectedByZeroFix:
    """DP-009/DP-010 touch only how raw factor values are read — they must
    not change containment behaviour. Mirrors test_daily_picks_containment.py
    but exercises the zero-score path specifically."""

    def test_meta_model_remains_shadow_computed_with_zero_scores(self):
        items = [_candidate("AAA", tech=0.0, fund=0.0, quality=0.0)]
        enriched, mock_predict = _run_zscore_and_rank(items, production_learning_enabled=False)
        assert mock_predict.called, "meta-model must still be invoked for shadow observability"
        assert enriched[0]["meta_alpha"] == -42.0
        assert enriched[0]["ranking_alpha"] == enriched[0]["combined_alpha"]
        assert enriched[0]["meta_alpha_used_for_ranking"] is False

    def test_ranking_alpha_uses_contained_path_with_zero_scores_flag_default(self, monkeypatch):
        # Flag intentionally left unset — proves the default containment
        # path (via services.alpha_engine.containment) still applies when a
        # zero-valued factor score is present. This test never sets
        # LEARNING_ALPHA_PRODUCTION_ENABLED=1.
        from services.alpha_engine import containment
        monkeypatch.delenv(containment.ENV_VAR, raising=False)
        items = [_candidate("AAA", quality=0.0)]
        with patch("services.alpha_engine.meta_model.predict", return_value=999.0):
            enriched = dp._zscore_and_rank(items, IC_WEIGHTS, REGIME, regime_id=0, market="US")
        assert enriched[0]["ranking_alpha"] == enriched[0]["combined_alpha"]
        assert enriched[0]["meta_alpha_used_for_ranking"] is False

    def test_meta_alpha_still_drives_ranking_when_explicitly_enabled_with_zero_scores(self):
        # Confirms the fix doesn't accidentally make containment *stricter*
        # either — explicit enablement (as already covered by
        # test_daily_picks_containment.py) still works with a zero-valued
        # factor present.
        items = [_candidate("AAA", tech=0.0)]
        enriched, mock_predict = _run_zscore_and_rank(items, production_learning_enabled=True)
        assert enriched[0]["ranking_alpha"] == -42.0
        assert enriched[0]["meta_alpha_used_for_ranking"] is True


class TestZscoreAndRankExistingNonZeroBehaviourUnchanged:
    def test_typical_nonzero_scores_produce_expected_zscores(self):
        # Sanity/regression check that ordinary (non-zero, non-missing)
        # scores are completely unaffected by removing the `or 50` fallback,
        # since `float(x)` and `float(x or 50)` are identical whenever x is
        # truthy.
        items = [
            _candidate("AAA", tech=60.0, fund=55.0, sentiment=50.0, quality=52.0),
            _candidate("BBB", tech=40.0, fund=80.0, sentiment=60.0, quality=48.0),
        ]
        enriched, _ = _run_zscore_and_rank(items)
        row_a = next(r for r in enriched if r["symbol"] == "AAA")
        row_b = next(r for r in enriched if r["symbol"] == "BBB")
        # tech: mean=50, std=10 -> z(60)=1.0, z(40)=-1.0
        assert row_a["factor_zscores"]["tech"] == pytest.approx(1.0, abs=1e-6)
        assert row_b["factor_zscores"]["tech"] == pytest.approx(-1.0, abs=1e-6)

    def test_single_item_list_uses_default_stats_and_is_unaffected(self):
        # len(vals) < 2 -> stats fall back to (50.0, 1.0) — a pre-existing,
        # unrelated code path (not the `or 50` line under test) that must
        # remain untouched.
        items = [_candidate("SOLO", tech=73.0)]
        enriched, _ = _run_zscore_and_rank(items)
        assert enriched[0]["factor_zscores"]["tech"] == pytest.approx(23.0, abs=1e-6)
