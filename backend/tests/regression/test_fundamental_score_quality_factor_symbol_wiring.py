"""
Issue #117 — `_fundamental_score` called `altman_zscore_signal(info, ticker)` /
`sloan_accruals_signal(info, ticker)` with an undefined `ticker` (flake8 F821).
Both calls sit inside `except Exception: pass`, so the NameError silently
disabled the Altman/Sloan balance-sheet signals. They must receive the
function's own `symbol`. Thresholds/weights are untouched: these tests only
prove wiring, that returned signals reach the existing balance-sheet/reason
logic, and that the existing failure tolerance is preserved.
"""
from unittest.mock import patch

from services.prediction_engine import PredictionEngine

_NEUTRAL = {"z_zone": "unavailable", "z_score": None, "accruals_ratio": None}


def _run(info, altman, sloan):
    engine = PredictionEngine()
    with patch("services.nse_pledge.get_promoter_pledge_pct", return_value=None), \
         patch("services.quality_factors.altman_zscore_signal", side_effect=altman) as a, \
         patch("services.quality_factors.sloan_accruals_signal", side_effect=sloan) as s:
        result = engine._fundamental_score(dict(info), "medium", market="IN", symbol="TESTSTOCK")
    return result, a, s


def test_altman_and_sloan_receive_exact_symbol(in_market_info):
    _, a, s = _run(in_market_info, lambda *_: dict(_NEUTRAL), lambda *_: dict(_NEUTRAL))
    assert a.call_count == 1 and a.call_args.args[1] == "TESTSTOCK"
    assert s.call_count == 1 and s.call_args.args[1] == "TESTSTOCK"


def test_returned_signals_reach_reasons_and_lower_the_score(in_market_info):
    base, _, _ = _run(in_market_info, lambda *_: dict(_NEUTRAL), lambda *_: dict(_NEUTRAL))
    hit, _, _ = _run(
        in_market_info,
        lambda *_: {"z_zone": "distress", "z_score": 1.1},
        lambda *_: {"accruals_ratio": 12},
    )
    assert any("Altman Z-Score 1.1 — Distress Zone" in r for r in hit["reasons"])
    assert any("High accruals (12%)" in r for r in hit["reasons"])
    assert hit["score"] < base["score"]


def test_quality_factor_failure_is_still_tolerated(in_market_info):
    def boom(*_):
        raise RuntimeError("provider down")
    result, _, _ = _run(in_market_info, boom, boom)
    assert isinstance(result["score"], (int, float))
    assert not any("Altman" in r or "accruals" in r.lower() for r in result["reasons"])
