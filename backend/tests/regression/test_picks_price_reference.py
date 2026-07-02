"""
Release 12A — generation-reference provenance and honest quote metadata.

Root cause (Daily Picks integrity audit): the entry-zone "moved since
generation" warning compared a Yahoo adjusted daily-history close (bar date
unrecorded) against an NSE/Finnhub last-traded quote with no timestamp,
session, or basis check — it could fire while the market was closed.

These tests prove the backend now persists WHERE the generation price came
from and passes through provider-supplied quote timestamps only (never the
request time), all additively and legacy-safe. Deterministic fixtures only —
no network, no providers, no production calls.
"""

from datetime import timezone
from unittest.mock import patch

import services.daily_picks as dp
import services.finnhub_client as fh
import services.nse_client as nse
from services.prediction_engine import PredictionEngine

ENGINE = PredictionEngine()


# ─────────────────────────────────────────────────────────────────────────────
# NSE quote timestamp parsing — exchange-supplied only, never invented
# ─────────────────────────────────────────────────────────────────────────────

def test_nse_update_time_parses_to_ist_iso():
    iso = nse._parse_nse_update_time("02-Jul-2026 15:59:59")
    assert iso == "2026-07-02T15:59:59+05:30"


def test_nse_update_time_never_invented():
    for bad in (None, "", "garbage", "2026-07-02 15:59:59", 12345):
        assert nse._parse_nse_update_time(bad) is None, f"input={bad!r}"


def test_nse_quote_carries_provenance_fields():
    payload = {
        "priceInfo": {"lastPrice": 3175.4, "previousClose": 3170.0},
        "info": {"companyName": "Mahindra & Mahindra"},
        "metadata": {"lastUpdateTime": "02-Jul-2026 15:59:59"},
    }

    class _Resp:
        status_code = 200
        def json(self):
            return payload

    with patch.object(nse, "_ensure_session"), \
         patch.object(nse, "_SESSION") as sess, \
         patch.dict(nse._quote_cache, {}, clear=True):
        sess.get.return_value = _Resp()
        q = nse.get_quote("M&M-R12ATEST")

    assert q["quote_source"] == "nse_official"
    assert q["quote_price_basis"] == "last_traded_unadjusted"
    assert q["quote_timestamp"] == "2026-07-02T15:59:59+05:30"


def test_nse_quote_missing_metadata_yields_null_timestamp():
    payload = {"priceInfo": {"lastPrice": 100.0, "previousClose": 99.0}, "info": {}}

    class _Resp:
        status_code = 200
        def json(self):
            return payload

    with patch.object(nse, "_ensure_session"), \
         patch.object(nse, "_SESSION") as sess, \
         patch.dict(nse._quote_cache, {}, clear=True):
        sess.get.return_value = _Resp()
        q = nse.get_quote("NOMETA-R12A")

    assert q["quote_timestamp"] is None
    assert q["quote_source"] == "nse_official"


# ─────────────────────────────────────────────────────────────────────────────
# Finnhub quote timestamp — provider "t" only
# ─────────────────────────────────────────────────────────────────────────────

def _finnhub_quote_with(data: dict, symbol: str):
    class _Resp:
        def raise_for_status(self):
            pass
        def json(self):
            return data

    with patch.object(fh, "FINNHUB_KEY", "test-key"), \
         patch.object(fh, "_SESSION") as sess, \
         patch.dict(fh._cache, {}, clear=True):
        sess.get.return_value = _Resp()
        return fh.get_quote(symbol, "US")


def test_finnhub_quote_timestamp_from_provider_epoch():
    q = _finnhub_quote_with({"c": 110.0, "pc": 100.0, "t": 1782072000}, "R12A1")
    assert q["quote_source"] == "finnhub"
    assert q["quote_price_basis"] == "last_traded_unadjusted"
    from datetime import datetime
    assert q["quote_timestamp"] == datetime.fromtimestamp(1782072000, tz=timezone.utc).isoformat()


def test_finnhub_quote_timestamp_null_when_absent_or_zero():
    for t in (0, None):
        data = {"c": 110.0, "pc": 100.0}
        if t is not None:
            data["t"] = t
        q = _finnhub_quote_with(data, f"R12A-{t}")
        assert q["quote_timestamp"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Daily Picks provenance persistence — additive, legacy-safe
# ─────────────────────────────────────────────────────────────────────────────

_BASE_RESULT = {
    "company_name": "TEST", "signal": "BUY", "current_price": 100.0,
    "target_price": 110.0, "confidence": 70,
    "trade_levels": {"entry_low": 99.0, "entry_high": 100.5,
                     "stop_loss": 95.0, "risk_reward_ratio": 2.0},
    "technical": {"score": 60}, "fundamental_score": {"score": 55},
    # Real unavailable-sentiment contract: compat score 50 stays in the source
    # response (pre-existing _build_summary requirement), data_available False.
    "sentiment_score": {"score": 50, "label": "NEUTRAL", "data_available": False},
    "quality_factors": {"score": 58}, "reasoning": [], "score_band": None,
    "global_context": {}, "market_regime": {},
    "composite_score": 62.0, "confidence_score": 70,
}


def _run_predict_stock(result: dict):
    class _FakeEngine:
        async def predict(self, symbol, market, horizon):
            return result

    with patch("services.daily_picks.PredictionEngine", return_value=_FakeEngine()), \
         patch("services.daily_picks.time.sleep"):
        return dp._predict_stock("R12ATEST", "short", "US")


def test_pick_persists_generation_reference_provenance():
    result = dict(_BASE_RESULT)
    result["generated_at"] = "2026-07-02T20:36:00+00:00"
    result["price_reference"] = {
        "price": 100.0, "source": "yahoo_daily_history",
        "price_basis": "adjusted_close", "as_of": "2026-07-02T00:00:00+05:30",
    }
    pick = _run_predict_stock(result)
    assert pick["generated_at"] == "2026-07-02T20:36:00+00:00"
    assert pick["generation_reference_price"] == 100.0
    assert pick["generation_reference_source"] == "yahoo_daily_history"
    assert pick["generation_reference_price_basis"] == "adjusted_close"
    assert pick["generation_reference_as_of"] == "2026-07-02T00:00:00+05:30"


def test_legacy_result_without_reference_stays_safe():
    pick = _run_predict_stock(dict(_BASE_RESULT))
    # Additive keys exist but are None — nothing fabricated for legacy shapes.
    assert pick["generation_reference_price"] is None
    assert pick["generation_reference_source"] is None
    assert pick["generation_reference_price_basis"] is None
    assert pick["generation_reference_as_of"] is None
    # Frozen model outputs are untouched.
    assert pick["price"] == 100.0
    assert pick["target"] == 110.0
    assert pick["entry_low"] == 99.0 and pick["entry_high"] == 100.5
    assert pick["stop_loss"] == 95.0
    assert pick["confidence"] == 70


# ─────────────────────────────────────────────────────────────────────────────
# Decision-model invariance — trade-level formulas unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_trade_level_formula_unchanged():
    levels = ENGINE._trade_levels(100.0, "BUY", 110.0, atr=2.0, horizon="short")
    # Entry zone stays the released narrow band around the generation price.
    assert levels["entry_low"] == round(100.0 - 2.0 * 0.3, 2)
    assert levels["entry_high"] == round(100.0 + 2.0 * 0.1, 2)
    assert levels["take_profit"] == 110.0
