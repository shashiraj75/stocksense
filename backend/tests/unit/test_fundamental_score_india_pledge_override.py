"""
Reliability Release 15E-B — behavior-level tests for the India NSE
promoter-pledge override inside `_fundamental_score()`.

Confirms the repaired method (now receiving a required keyword-only
`symbol`, see `prediction_engine.py:1571`) correctly wires `symbol` into
`services.nse_pledge.get_promoter_pledge_pct`, and that the existing
None/exception fallback-to-Screener-value behavior is unchanged. No real
NSE request — `get_promoter_pledge_pct` is mocked in every case.
"""

from unittest.mock import patch

from services.prediction_engine import PredictionEngine


def test_india_pledge_override_nse_value_overrides_screener(in_market_info):
    info = dict(in_market_info)
    info["_screener_data"] = dict(info["_screener_data"], promoter_pledge_pct=20.0)
    engine = PredictionEngine()

    with patch("services.nse_pledge.get_promoter_pledge_pct", return_value=60.0) as mock_nse:
        result = engine._fundamental_score(info, "medium", market="IN", symbol="TESTSTOCK")

    mock_nse.assert_called_once_with("TESTSTOCK")
    assert any("High promoter pledge (60.0%)" in r for r in result["reasons"])


def test_india_pledge_override_nse_none_keeps_screener_value(in_market_info):
    info = dict(in_market_info)
    info["_screener_data"] = dict(info["_screener_data"], promoter_pledge_pct=30.0)
    engine = PredictionEngine()

    with patch("services.nse_pledge.get_promoter_pledge_pct", return_value=None) as mock_nse:
        result = engine._fundamental_score(info, "medium", market="IN", symbol="TESTSTOCK")

    mock_nse.assert_called_once_with("TESTSTOCK")
    assert any("Elevated promoter pledge (30.0%)" in r for r in result["reasons"])


def test_india_pledge_override_nse_exception_keeps_screener_value(in_market_info):
    info = dict(in_market_info)
    info["_screener_data"] = dict(info["_screener_data"], promoter_pledge_pct=0.0)
    engine = PredictionEngine()

    with patch("services.nse_pledge.get_promoter_pledge_pct", side_effect=Exception("boom")) as mock_nse:
        result = engine._fundamental_score(info, "medium", market="IN", symbol="TESTSTOCK")

    mock_nse.assert_called_once_with("TESTSTOCK")
    assert any("Zero promoter pledge" in r for r in result["reasons"])


def test_us_market_never_calls_nse_pledge_lookup(base_info):
    info = dict(base_info)
    engine = PredictionEngine()

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("NSE pledge lookup must not be called for US market")

    with patch("services.nse_pledge.get_promoter_pledge_pct", side_effect=_fail_if_called) as mock_nse:
        engine._fundamental_score(info, "medium", market="US", symbol="AAPL")

    mock_nse.assert_not_called()
