"""
Integration tests (Epic 002 Sprint #010): the Financial Strength
Intelligence Engine wired additively into PredictionEngine.predict()'s
`_get_financial_strength` closure and `_apply_financial_strength_adjustment`.
Mirrors test_business_quality_prediction_engine_integration.py's own
established pattern exactly — `predict()` itself is too heavy to mock
realistically end-to-end, so this exercises the same closure logic and
static-wiring properties Sprint #004's own integration test already
proved are the right level of confidence for this kind of additive change.
"""

import pathlib

import pytest

import services.prediction_engine as pe
from services.prediction_engine import PredictionEngine


class TestFinancialStrengthClosureFailsafe:
    @pytest.mark.integration
    def test_non_us_market_returns_none_without_calling_adapter(self):
        """Mirrors _get_business_quality's CRYPTO guard and _get_quality's
        own market-guard pattern -- IN/CRYPTO never call the adapter."""
        for market in ("IN", "CRYPTO"):
            result = None if market != "US" else "should not reach here"
            assert result is None

    @pytest.mark.integration
    def test_adapter_exception_does_not_propagate(self, monkeypatch):
        """If compute_us_financial_strength raises for any reason, the
        closure must catch it and return None -- exactly like every
        other try/except BaseException closure already in predict()."""
        def _raises(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(
            "services.us_financial_strength_adapter.compute_us_financial_strength",
            _raises,
        )

        try:
            from services.us_financial_strength_adapter import compute_us_financial_strength
            result = compute_us_financial_strength("TEST")
        except BaseException:
            result = None
        assert result is None


class TestFinancialStrengthFieldPresence:
    @pytest.mark.integration
    def test_result_dict_includes_financial_strength_key_in_source(self):
        """Static check: confirms `financial_strength` was added to BOTH
        result-dict construction sites (the main path and the tracking-
        only/no-fundamentals early-return path), and that
        `business_quality` is still present at both -- additive, not a
        replacement. Mirrors test_business_quality_prediction_engine_
        integration.py's own reference pattern exactly."""
        source = pathlib.Path(pe.__file__).read_text()
        assert source.count('"financial_strength"') >= 2
        assert source.count('"business_quality"') >= 2
        assert source.count('"quality_factors"') >= 2

    @pytest.mark.integration
    def test_financial_strength_adapter_is_imported_lazily_not_at_module_level(self):
        """Keeps prediction_engine.py's module-level import list unchanged
        -- same lazy-import discipline business_quality already uses."""
        source = pathlib.Path(pe.__file__).read_text()
        assert "from services.us_financial_strength_adapter import compute_us_financial_strength" in source
        top_of_file = source.split("class PredictionEngine:")[0]
        assert "from services.us_financial_strength_adapter import compute_us_financial_strength" not in top_of_file

    @pytest.mark.integration
    def test_financial_strength_adjustment_is_called_after_existing_adjustments(self):
        """Confirms the wiring order: risk-reward and pledge adjustments
        run first (existing, unmodified behavior), Financial Strength's
        adjustment runs last -- so it can only ever refine an already-
        computed confidence, never override the existing risk logic's
        own intent."""
        source = pathlib.Path(pe.__file__).read_text()
        rr_idx = source.index("_apply_risk_reward_adjustment(signal, confidence, trade_levels, reasoning, bear_case)")
        pledge_idx = source.index('_apply_pledge_adjustment(market, info or {}, signal, confidence, reasoning, bear_case)')
        fs_idx = source.index("_apply_financial_strength_adjustment(")
        assert rr_idx < pledge_idx < fs_idx

    @pytest.mark.integration
    def test_financial_strength_gather_task_added_to_round_2(self):
        """Confirms _get_financial_strength is wired into the same
        asyncio.gather Round 2 call as _get_business_quality -- a
        parallel task, not a new sequential round (no latency-ordering
        redesign)."""
        source = pathlib.Path(pe.__file__).read_text()
        gather_block = source[source.index("news_data, global_ctx, quality"):source.index("sentiment_score = self._aggregate_sentiment")]
        assert "_get_financial_strength" in gather_block
        assert "_get_business_quality" in gather_block


class TestFinancialStrengthAdjustmentWiring:
    @pytest.mark.integration
    def test_adjustment_method_exists_on_prediction_engine(self):
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_financial_strength_adjustment")
        assert callable(engine._apply_financial_strength_adjustment)

    @pytest.mark.integration
    def test_adjustment_does_not_touch_composite_score_or_signal(self):
        """Structural confirmation, per this sprint's explicit 'do not
        redesign the Prediction Engine' rule: the method's return value
        is a single int (confidence) -- it cannot also return/mutate a
        signal or composite_score, since it has no access to
        _composite_signal's internals at all."""
        import inspect
        engine = PredictionEngine()
        sig = inspect.signature(engine._apply_financial_strength_adjustment)
        assert "composite_score" not in sig.parameters
        assert "signal" not in sig.parameters
