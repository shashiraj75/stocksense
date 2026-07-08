"""
Phase 2 spec §8/§9 regression suite:

- cache safety: composing from the REAL shared _pred_cache entry leaves
  it identity- and byte-for-byte unchanged (flag on);
- shared-shape regression (closes Phase 1's named risk 1): AST-extract
  the predict() result-literal keys and assert the builder's mapping
  tables and the shared fixture against them — a Prediction Engine key
  rename becomes a loud, attributed failure here, with zero engine
  execution and zero network;
- the router's approved call site exists exactly as specified.
"""

import ast
import copy
import json
import time
from pathlib import Path

import services.research_analyst.snapshot_builder as sb
from services.research_analyst.research_composer import compose_prediction_response_with_research
from tests.fixtures.research_analyst_fixtures import make_predict_result

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_FLAG = "RESEARCH_ANALYST_V2_ENABLED"


class TestRealPredCacheSafetyWithComposer:
    def test_cache_entry_unchanged_by_flag_on_composition(self, monkeypatch):
        from services.prediction_engine import _pred_cache

        monkeypatch.setenv(_FLAG, "1")
        key = "___research_composer_test___:IN:medium"
        fixture = make_predict_result()
        try:
            _pred_cache[key] = (time.time(), fixture)
            before = copy.deepcopy(fixture)
            before_json = json.dumps(before, sort_keys=True, default=str)

            out = compose_prediction_response_with_research(
                _pred_cache[key][1], symbol="RELIANCE", market="IN", horizon="medium")

            cached = _pred_cache[key][1]
            assert cached is fixture and cached == before
            assert json.dumps(cached, sort_keys=True, default=str) == before_json
            assert out is not cached and "research_report" in out
            assert "research_report" not in cached
        finally:
            _pred_cache.pop(key, None)


def _predict_result_literal_keys() -> set[str]:
    """AST-extract the top-level string keys of the FULL result dict
    literal assembled in PredictionEngine.predict(). Anchor: dict
    literals containing both "signal" and "generated_at" keys; among
    those (the success path and the smaller error path), the one with
    the most keys is the full result. Sanity-checked below."""
    source = (BACKEND_ROOT / "services" / "prediction_engine.py").read_text(encoding="utf-8")
    candidates: list[set[str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"signal", "generated_at"} <= keys:
                candidates.append(keys)
    assert candidates, "could not locate any predict() result dict literal"
    full = max(candidates, key=len)
    assert "weights_used" in full, "anchor drifted: largest literal is not the full result dict"
    return full


class TestSharedShapeRegression:
    def test_builder_mapping_keys_exist_in_real_predict_output(self):
        real_keys = _predict_result_literal_keys()
        mapped = set(sb._DECISION_CONTEXT_KEYS)
        mapped |= {key for _, key, _ in sb._ENGINE_OWNERS}
        mapped |= set(sb._MARKET_CONTEXT_KEYS)
        mapped |= {"generated_at", "data_timestamp", "symbol", "market", "horizon",
                   "bull_case", "bear_case"}
        missing = sorted(mapped - real_keys)
        assert not missing, (
            f"builder mapping keys no longer present in predict()'s result literal: {missing} — "
            f"a Prediction Engine key rename would silently degrade snapshots to MISSING")

    def test_fixture_declares_no_key_absent_from_real_output(self):
        real_keys = _predict_result_literal_keys()
        fixture_keys = set(make_predict_result().keys())
        drift = sorted(fixture_keys - real_keys)
        assert not drift, f"fixture declares keys the real predict() result does not have: {drift}"


class TestApprovedCallSite:
    def test_predictions_router_call_site_shape(self):
        """The approved call site exists, is flag-gated, and there is
        exactly one composer invocation in the router."""
        source = (BACKEND_ROOT / "api" / "routers" / "predictions.py").read_text(encoding="utf-8")
        assert source.count("compose_prediction_response_with_research(") == 1
        gate_idx = source.index("if research_analyst_v2_enabled():")
        call_idx = source.index("compose_prediction_response_with_research(", gate_idx)
        # the call sits inside the flag gate, within a few lines of it
        assert 0 < call_idx - gate_idx < 400
        # and runs after the RCI composer block, per the Phase 2 spec §6
        assert source.index("compose_prediction_response_with_rci(") < gate_idx
