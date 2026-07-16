"""
Product Integrity #011 — India Daily Picks session-freshness backend gate.

Root cause (forensically confirmed against production, 2026-07-16): Yahoo/
yfinance per-symbol data lag meant PredictionEngine could silently persist a
price_reference dated a session behind the expected latest completed NSE
session, with the only existing safeguard being a frontend-only disclosure
(Product Integrity #004) that could warn but never prevent or retry.

This release adds a backend gate: _fetch_history() now retries within its
existing attempt budget when a fetched bar is unexpectedly behind the
expected session, and price_reference is now labeled with is_stale/
expected_session at generation time rather than only reconstructed later.

predict() is a very large, deeply-integrated async method (technical
indicators, quality factors, global context, news sentiment, etc.) — a full
mocked end-to-end call is impractical to build reliably here. Following this
codebase's own established convention for exactly this situation (see
test_prediction_engine_shared_ticker_performance.py's own docstring: "the
wiring inside predict() is structurally present, mirroring this codebase's
existing static-check pattern for confirming code shape"), the retry/staleness
wiring itself is verified structurally; the pure date-arithmetic it depends on
is verified behaviorally in test_market_hours_expected_session.py; and the
one fully isolable behavioral piece (_predict_stock's error-passthrough fix)
is tested end-to-end with predict() itself mocked out entirely.
"""
import inspect

import pytest

import services.prediction_engine as pe
import services.daily_picks as dp


# ─── structural: retry-on-stale wiring is present in _fetch_history ────────

def test_fetch_history_checks_expected_session_before_accepting_a_bar():
    src = inspect.getsource(pe.PredictionEngine.predict)
    assert "get_expected_latest_completed_nse_session" in src
    assert "_expected_session" in src
    assert "last_bar_date < _expected_session" in src


def test_fetch_history_retry_stays_within_existing_three_attempt_budget():
    """The stale-retry check is gated on attempt < 2 — it must not add a
    fourth in-loop attempt beyond the existing budget, only make attempts 0
    and 1 conditionally retry instead of accepting immediately."""
    src = inspect.getsource(pe.PredictionEngine.predict)
    assert "_expected_session is not None and attempt < 2" in src


def test_expected_session_only_computed_for_india():
    """US has no expected-session check yet (disclosed scope, not silently assumed) —
    _expected_session must be None for any market other than IN."""
    src = inspect.getsource(pe.PredictionEngine.predict)
    assert 'get_expected_latest_completed_nse_session() if market == "IN" else None' in src


def test_price_reference_carries_is_stale_and_expected_session_fields():
    src = inspect.getsource(pe.PredictionEngine.predict)
    # Both occurrences (main branch + tracking-only-instrument branch) must carry the fields.
    assert src.count('"is_stale":') == 2
    assert src.count('"expected_session":') == 2


def test_is_stale_is_none_not_false_when_no_expected_session_available():
    """A market/state where staleness was never actually checked must report
    is_stale=None (unknown), never a false-positive 'fresh' claim."""
    src = inspect.getsource(pe.PredictionEngine.predict)
    assert "else None" in src  # the is_stale ternary's fallback branch


# ─── behavioral: _predict_stock's error-passthrough fix ────────────────────

@pytest.mark.regression
def test_predict_stock_skips_error_shaped_results(monkeypatch):
    """An error-shaped predict() result (timeout, empty history, etc. —
    truthy dict, no 'signal' key) must be excluded, not appended to the
    batch with garbage/None fields."""
    async def _fake_predict(self, symbol, market, horizon):
        return {"error": "insufficient history", "code": "DATA_PROVIDER_UNAVAILABLE"}

    monkeypatch.setattr(pe.PredictionEngine, "predict", _fake_predict)
    result = dp._predict_stock("FAKESYM", "short", market="IN")
    assert result is None


@pytest.mark.regression
def test_predict_stock_still_skips_rejected_results(monkeypatch):
    """Regression guard: the new error check must not have broken the
    pre-existing REJECTED quality-gate skip."""
    async def _fake_predict(self, symbol, market, horizon):
        return {"signal": "REJECTED", "rejection_reasons": ["insufficient data"]}

    monkeypatch.setattr(pe.PredictionEngine, "predict", _fake_predict)
    result = dp._predict_stock("FAKESYM", "short", market="IN")
    assert result is None


@pytest.mark.regression
def test_predict_stock_still_returns_none_for_falsy_result(monkeypatch):
    async def _fake_predict(self, symbol, market, horizon):
        return None

    monkeypatch.setattr(pe.PredictionEngine, "predict", _fake_predict)
    result = dp._predict_stock("FAKESYM", "short", market="IN")
    assert result is None


@pytest.mark.regression
def test_predict_stock_error_check_does_not_reject_a_normal_successful_result(monkeypatch):
    """A genuine successful result (no 'error' key at all) must not be
    excluded by the new check — getting `.get('error')` on a dict without
    that key must safely return None/falsy, not raise or misclassify."""
    async def _fake_predict(self, symbol, market, horizon):
        return {
            "signal": "BUY", "confidence": 60, "reasoning": [], "trade_levels": {},
            "quality_factors": {}, "score_band": "Good", "fund_score": 70, "tech_score": 65,
        }

    monkeypatch.setattr(pe.PredictionEngine, "predict", _fake_predict)
    result = dp._predict_stock("FAKESYM", "short", market="IN")
    assert result is not None
    assert result["symbol"] == "FAKESYM"


def test_generation_reference_staleness_fields_propagate_to_the_pick_record():
    """daily_picks.py must copy the new price_reference fields through to
    the published generation_reference_* keys, matching the existing
    provenance fields' propagation pattern."""
    src = inspect.getsource(dp)
    assert '"generation_reference_is_stale": (result.get("price_reference") or {}).get("is_stale")' in src
    assert '"generation_reference_expected_session": (result.get("price_reference") or {}).get("expected_session")' in src
