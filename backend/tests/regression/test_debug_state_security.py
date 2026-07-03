"""
Release 14B — /api/predictions/debug/state access control and response
narrowing.

Root cause (Release 14A audit): the endpoint was fully unauthenticated and
returned raw `cache_keys` (a real-time symbol/market/horizon traffic
fingerprint) and raw `log` lines (which leaked the complete internal
prediction-result schema in production, confirmed live). This release
requires the same proven X-Secret model already used by
/api/picks/generate, and permanently removes every field that could reveal
symbols, markets, horizons, cache-entry identifiers, error text, or
internal schema — regardless of authentication.

Deterministic only: no network, no live clock dependence beyond controlled
cache-age fixtures, no external providers.
"""

import asyncio
import time

import pytest

import api.routers.predictions as predictions_router
from services.prediction_engine import _pred_cache, _cache_set


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fixed secret for deterministic auth tests; counters reset; cache
    entries added by a test are cleaned up automatically."""
    monkeypatch.setattr(predictions_router, "_DEBUG_SECRET", "test-debug-secret")
    predictions_router._rci_composition_success_count = 0
    predictions_router._rci_fail_open_count = 0
    before_keys = set(_pred_cache.keys())
    yield
    for k in set(_pred_cache.keys()) - before_keys:
        del _pred_cache[k]
    predictions_router._rci_composition_success_count = 0
    predictions_router._rci_fail_open_count = 0


def _call(x_secret=None):
    return asyncio.run(predictions_router.debug_state(x_secret=x_secret))


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestAuthentication:
    """Fail-closed matrix: /debug/state must return 200 only when BOTH the
    configured secret and the supplied header are non-empty, non-whitespace,
    and exactly equal after stripping. Every other combination — including
    a blank secret paired with a blank header, which /api/picks/generate's
    plain `!=` comparison would accept — must be rejected with a generic
    401 and no diagnostic detail."""

    def _assert_401(self, x_secret):
        with pytest.raises(Exception) as exc_info:
            _call(x_secret=x_secret)
        err = exc_info.value
        assert getattr(err, "status_code", None) == 401
        detail = str(getattr(err, "detail", ""))
        assert "cache" not in detail.lower()
        assert "thread" not in detail.lower()
        assert "rci" not in detail.lower()

    # ── PICKS_SECRET missing (empty string, the os.getenv default) ─────────
    def test_missing_secret_no_header_rejected(self, monkeypatch):
        monkeypatch.setattr(predictions_router, "_DEBUG_SECRET", "")
        self._assert_401(x_secret=None)

    def test_missing_secret_blank_header_rejected(self, monkeypatch):
        monkeypatch.setattr(predictions_router, "_DEBUG_SECRET", "")
        self._assert_401(x_secret="")

    # ── PICKS_SECRET empty/whitespace-only ──────────────────────────────────
    def test_whitespace_only_secret_no_header_rejected(self, monkeypatch):
        monkeypatch.setattr(predictions_router, "_DEBUG_SECRET", "   ")
        self._assert_401(x_secret=None)

    def test_whitespace_only_secret_blank_header_rejected(self, monkeypatch):
        monkeypatch.setattr(predictions_router, "_DEBUG_SECRET", "   ")
        self._assert_401(x_secret="   ")

    # ── PICKS_SECRET configured and non-empty ───────────────────────────────
    def test_valid_secret_no_header_rejected(self):
        self._assert_401(x_secret=None)

    def test_valid_secret_blank_header_rejected(self):
        self._assert_401(x_secret="")

    def test_valid_secret_whitespace_header_rejected(self):
        self._assert_401(x_secret="   ")

    def test_valid_secret_wrong_header_rejected(self):
        self._assert_401(x_secret="totally-wrong-secret")

    # ── The only accepting case ──────────────────────────────────────────
    def test_correct_header_is_accepted(self):
        state = _call(x_secret="test-debug-secret")
        assert isinstance(state, dict)
        assert "rci_observability" in state

    def test_secret_with_surrounding_whitespace_still_matches_after_strip(self):
        # Reasonable operational tolerance — a header value with incidental
        # leading/trailing whitespace (common with some proxies/copy-paste)
        # still authenticates; this is NOT a relaxation of the fail-closed
        # rule, since both sides must still be non-empty after stripping.
        state = _call(x_secret="  test-debug-secret  ")
        assert isinstance(state, dict)


@pytest.mark.regression
class TestPicksGenerateAuthenticationUnchanged:
    """Release 14B must not touch /api/picks/generate's own protection."""

    def test_picks_generate_still_uses_its_own_independent_secret_check(self):
        import api.routers.picks as picks_router
        import inspect
        src = inspect.getsource(picks_router.trigger_generation)
        assert "x_secret != PICKS_SECRET" in src
        # predictions.py's debug secret must not have been substituted in.
        assert "_DEBUG_SECRET" not in src


# ─────────────────────────────────────────────────────────────────────────────
# Response narrowing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestResponseNarrowing:
    def test_raw_cache_keys_never_returned(self):
        _cache_set(_pred_cache, "SECRETSYM:US:short", (time.time(), {"signal": "BUY"}))
        state = _call(x_secret="test-debug-secret")
        assert "cache_keys" not in state
        blob = str(state)
        assert "SECRETSYM" not in blob

    def test_raw_log_never_returned(self):
        predictions_router._bglog("[bg_thread] DONE SOMESYMBOL:IN:medium result_keys=['signal','target_price']")
        state = _call(x_secret="test-debug-secret")
        assert "log" not in state
        blob = str(state)
        assert "SOMESYMBOL" not in blob
        assert "result_keys" not in blob

    def test_no_symbol_market_horizon_or_schema_detail_leaks(self):
        _cache_set(_pred_cache, "AAPL:US:medium", (time.time(), {"target_price": 1}))
        predictions_router._bglog("[bg_thread] FINALLY AAPL:US:medium in_cache=True")
        state = _call(x_secret="test-debug-secret")
        blob = str(state)
        for leaked in ("AAPL", "US:medium", "target_price", "in_cache", "Exception", "Traceback"):
            assert leaked not in blob, f"leaked token found: {leaked!r}"

    def test_allowed_field_shape_matches_documented_contract(self):
        state = _call(x_secret="test-debug-secret")
        assert set(state.keys()) == {
            "computing_count", "cache_entry_count", "cache_age_summary_s",
            "thread_count", "rci_observability",
        }
        assert isinstance(state["computing_count"], int)
        assert isinstance(state["cache_entry_count"], int)
        assert set(state["cache_age_summary_s"].keys()) == {"min", "max", "avg"}
        assert isinstance(state["thread_count"], int)
        assert set(state["rci_observability"].keys()) == {
            "composition_success_count", "fail_open_count",
        }

    def test_empty_cache_summary_is_null(self):
        for k in list(_pred_cache.keys()):
            del _pred_cache[k]
        state = _call(x_secret="test-debug-secret")
        assert state["cache_entry_count"] == 0
        assert state["cache_age_summary_s"] == {"min": None, "max": None, "avg": None}

    def test_populated_cache_summary_is_finite_numeric(self):
        now = time.time()
        _cache_set(_pred_cache, "A:US:short", (now - 10, {}))
        _cache_set(_pred_cache, "B:US:short", (now - 30, {}))
        state = _call(x_secret="test-debug-secret")
        summary = state["cache_age_summary_s"]
        for v in summary.values():
            assert isinstance(v, (int, float)) and math_isfinite(v)
        assert summary["min"] <= summary["avg"] <= summary["max"]
        assert state["cache_entry_count"] >= 2


def math_isfinite(v) -> bool:
    import math
    return math.isfinite(v)


# ─────────────────────────────────────────────────────────────────────────────
# Regression and isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestRegressionAndIsolation:
    def test_rci_observability_still_available_to_authenticated_caller(self):
        predictions_router._rci_composition_success_count = 5
        predictions_router._rci_fail_open_count = 2
        state = _call(x_secret="test-debug-secret")
        assert state["rci_observability"] == {
            "composition_success_count": 5,
            "fail_open_count": 2,
        }

    def test_health_endpoint_remains_public_and_unchanged(self):
        import api.main as main_module
        import inspect
        src = inspect.getsource(main_module.health)
        assert "Header" not in src
        assert "_DEBUG_SECRET" not in src
        assert main_module.health() == {"status": "ok", "version": "1.0.0"}

    def test_daily_picks_module_does_not_reference_debug_secret(self):
        import pathlib
        import services.daily_picks as dp
        source = pathlib.Path(dp.__file__).read_text()
        assert "_DEBUG_SECRET" not in source

    def test_no_new_authentication_on_other_prediction_routes(self):
        import inspect
        src = inspect.getsource(predictions_router.get_prediction)
        assert "_DEBUG_SECRET" not in src
        assert "x_secret" not in inspect.signature(predictions_router.get_prediction).parameters
