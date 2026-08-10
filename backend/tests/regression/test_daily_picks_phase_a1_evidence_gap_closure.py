"""
Daily Picks Phase A1 Evidence-Gap Closure — for FUTURE Paper Trades.

GO finding: `_predict_stock()` in `services/daily_picks.py` calls the exact
same `PredictionEngine.predict()` used by the Stock Detail/Research path, so
`result["technical"]["overall"]` (the governed BUY/SELL/HOLD technical
signal from `get_signal_summary()`) and `result["sentiment_score"]["score"]`
(the genuine numeric sentiment score) are the SAME authoritative values —
not proxies. This closes the Daily Picks evidence gap by exposing both
additively on the candidate dict `_predict_stock()` returns, so they flow
into the published Daily Picks payload and, from there, into
`buildEntryEvidenceFromDailyPick` on Buy.

Non-fabrication invariant under test: neither field is ever derived from
`tech_score` (threshold conversion) or the `sentiment` label
(label-to-number mapping) — both stay exactly what PredictionEngine itself
produced, or None when PredictionEngine didn't produce them.
"""

from unittest.mock import patch

import services.daily_picks as dp


def _predict_stock_with(technical=None, sentiment=None, market="US"):
    technical = technical if technical is not None else {"score": 60, "overall": "BUY"}
    sentiment = sentiment if sentiment is not None else {"score": 55.0, "data_available": True}

    async def fake_predict(self, symbol, market_, horizon):
        return {
            "signal": "BUY", "confidence": 80, "current_price": 100.0,
            "reasoning": [], "trade_levels": {}, "quality_factors": {"score": 50},
            "technical": technical,
            "fundamental_score": {"score": 55},
            "sentiment_score": sentiment,
            "price_reference": {
                "price": 100.0, "source": "yahoo_daily_history",
                "price_basis": "adjusted_close", "as_of": "2026-07-10T00:00:00",
            },
        }

    with patch.object(dp.PredictionEngine, "predict", fake_predict):
        return dp._predict_stock("EVDTEST", "short", market)


class TestTechnicalSignalPropagation:
    def test_genuine_governed_technical_signal_is_returned(self):
        cand = _predict_stock_with(technical={"score": 72, "overall": "BUY"})
        assert cand["technical_signal"] == "BUY"

    def test_sell_and_hold_pass_through_unmodified(self):
        assert _predict_stock_with(technical={"score": 20, "overall": "SELL"})["technical_signal"] == "SELL"
        assert _predict_stock_with(technical={"score": 50, "overall": "HOLD"})["technical_signal"] == "HOLD"

    def test_missing_technical_dict_stays_null_not_fabricated(self):
        cand = _predict_stock_with(technical={})
        assert cand["technical_signal"] is None
        # tech_score's own neutral fallback (50) must NOT leak into technical_signal
        assert cand["tech_score"] == 50

    def test_technical_signal_is_not_derived_from_tech_score(self):
        # A high tech_score with no "overall" key must NOT cause technical_signal
        # to be fabricated via any threshold — proves no conversion logic exists.
        cand = _predict_stock_with(technical={"score": 95})
        assert cand["technical_signal"] is None
        assert cand["tech_score"] == 95


class TestSentimentScorePropagation:
    def test_genuine_numeric_sentiment_score_is_returned(self):
        cand = _predict_stock_with(sentiment={"score": 63.5, "data_available": True, "label": "BULLISH"})
        assert cand["sentiment_score"] == 63.5
        assert cand["sentiment"] == "BULLISH"

    def test_unavailable_sentiment_stays_null(self):
        cand = _predict_stock_with(sentiment={"score": 50, "data_available": False, "label": "NEUTRAL"})
        assert cand["sentiment_score"] is None

    def test_genuine_zero_sentiment_score_is_preserved_not_dropped(self):
        cand = _predict_stock_with(sentiment={"score": 0.0, "data_available": True, "label": "BEARISH"})
        assert cand["sentiment_score"] == 0.0
        assert cand["sentiment_score"] is not None

    def test_sentiment_score_is_not_derived_from_label(self):
        # Same label, different genuine numeric scores must produce different
        # sentiment_score values — proves no label->number mapping table exists.
        low = _predict_stock_with(sentiment={"score": 12.0, "data_available": True, "label": "BEARISH"})
        high = _predict_stock_with(sentiment={"score": 38.0, "data_available": True, "label": "BEARISH"})
        assert low["sentiment_score"] == 12.0
        assert high["sentiment_score"] == 38.0
        assert low["sentiment_score"] != high["sentiment_score"]


class TestPublishedPayloadIntegrity:
    def test_new_fields_are_not_stripped_before_publication(self):
        # _ALPHA_OBS_ONLY_KEYS controls what's stripped from picks[horizon];
        # the new fields must NOT be in that stripped set.
        assert "technical_signal" not in dp._ALPHA_OBS_ONLY_KEYS
        assert "sentiment_score" not in dp._ALPHA_OBS_ONLY_KEYS

    def test_ranking_relevant_fields_unaffected_by_new_keys(self):
        # Adding technical_signal must not change any ranking-relevant field.
        cand = _predict_stock_with(technical={"score": 72, "overall": "BUY"})
        assert cand["tech_score"] == 72
        assert cand["signal"] == "BUY"
        assert cand["confidence"] == 80
