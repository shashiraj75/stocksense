"""
Reliability Release 15E-B — regression lock on the two exact wiring
guarantees the repair depends on: `_deep_fundamental_score` receiving a
real `info` object, and `_fundamental_score` receiving a real `symbol`.
Targeted structural inspection only, mirroring the repository's existing
regression-test pattern (e.g. test_recommendation_consolidation_api_cache_safety.py)
for locking in a specific, narrow source-level invariant — not a general
source-hash test.
"""

import inspect
import pathlib

import services.prediction_engine as pe


def test_deep_fundamental_repair_wires_info_into_method_and_caller():
    sig = inspect.signature(pe.PredictionEngine._deep_fundamental_score)
    assert "info" in sig.parameters

    source = pathlib.Path(pe.__file__).read_text()
    assert "self._deep_fundamental_score(shared_statement_ticker, horizon, info)" in source


def test_india_pledge_repair_requires_keyword_symbol_and_predict_passes_it():
    sig = inspect.signature(pe.PredictionEngine._fundamental_score)
    symbol_param = sig.parameters["symbol"]
    assert symbol_param.kind == inspect.Parameter.KEYWORD_ONLY
    assert symbol_param.default is inspect.Parameter.empty

    source = pathlib.Path(pe.__file__).read_text()
    assert "self._fundamental_score(info, horizon, market, symbol=symbol)" in source
