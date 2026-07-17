"""
Connects Daily Picks' displayed confidence to real, walk-forward validated
track record (2026-07-17) — additive only.

Root problem: a BUY pick's confidence % (prediction_engine.py's linear
composite-score rescale) says nothing about whether that horizon's signals
have actually been reliable. A short-term pick can show 90-100% confidence
even though short-term walk-forward validation (six runs this session,
~30k combined signals) showed a beat-benchmark rate as low as 36-38% —
worse than a coin flip. Users had no visibility into this gap.

Deliberately NOT wired to prediction_engine.py's confidence_score /
historical_factor_reliability — investigated and rejected: that component's
only real-data path reads from the same legacy IC-training join the
2026-07-12 forensic audit found contaminated, and is live for exactly one
of six market/horizon combinations in production (confirmed via direct
production query: IN/short has 3,870 training rows; every other
combination has 0-41, all under the 60-row activation threshold — so it
silently defaults to a flat, meaningless 50 everywhere else). Building a
"real reliability" feature on top of contaminated, inconsistently-active
data would not have solved anything.

Fix: services.validation_engine.get_track_record_summary(market, horizon)
reads val_runs (the walk-forward backtest table these confidence-vs-hit-rate
numbers were verified against directly, this session) and surfaces it as a
new, additive `historical_track_record` key on GET /api/picks/daily. Does
not touch confidence, the composite-score formula, the 25% BUY cutoff, or
any existing field on the picks payload.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ─── get_track_record_summary (pure data-fetching, no HTTP) ────────────────

@pytest.mark.regression
class TestGetTrackRecordSummary:
    def test_returns_one_entry_per_available_universe_for_india(self):
        from services.validation_engine import get_track_record_summary

        def fake_results(horizon, universe):
            if universe == "nifty100":
                return {"available": True, "beat_benchmark_pct": 49.4, "buy_hit_rate_pct": 55.3,
                        "buy_signals": 4995, "run_at": "2026-07-17T08:49:43Z"}
            return {"available": False, "message": "No validation run found. Run /api/validation/run first."}

        with patch("services.validation_engine.get_latest_results", side_effect=fake_results):
            result = get_track_record_summary("IN", "medium")

        assert len(result) == 1
        assert result[0]["universe"] == "nifty100"
        assert result[0]["beat_benchmark_pct"] == 49.4
        assert result[0]["buy_hit_rate_pct"] == 55.3
        assert result[0]["n_signals"] == 4995
        assert result[0]["run_at"] == "2026-07-17T08:49:43Z"

    def test_includes_both_india_universes_when_both_available(self):
        from services.validation_engine import get_track_record_summary
        with patch("services.validation_engine.get_latest_results",
                   return_value={"available": True, "beat_benchmark_pct": 45.0,
                                  "buy_hit_rate_pct": 50.0, "buy_signals": 100, "run_at": "t"}):
            result = get_track_record_summary("IN", "short")
        assert {r["universe"] for r in result} == {"nifty100", "midcap"}

    def test_us_market_only_checks_us_universe_never_nifty_or_midcap(self):
        from services.validation_engine import get_track_record_summary
        calls = []

        def fake_results(horizon, universe):
            calls.append(universe)
            return {"available": True, "beat_benchmark_pct": 38.2, "buy_hit_rate_pct": 50.2,
                    "buy_signals": 2030, "run_at": "t"}

        with patch("services.validation_engine.get_latest_results", side_effect=fake_results):
            result = get_track_record_summary("US", "short")

        assert calls == ["us"]
        assert len(result) == 1
        assert result[0]["universe"] == "us"

    def test_empty_list_when_no_universe_has_a_run_yet(self):
        from services.validation_engine import get_track_record_summary
        with patch("services.validation_engine.get_latest_results",
                   return_value={"available": False, "message": "No validation run found."}):
            result = get_track_record_summary("IN", "long")
        assert result == []

    def test_swallows_a_lookup_failure_for_one_universe_without_raising(self):
        """A genuine exception looking up one universe (e.g. a transient DB
        error) must not take down the whole summary — matches
        get_latest_results()'s own fail-soft contract."""
        from services.validation_engine import get_track_record_summary

        def fake_results(horizon, universe):
            if universe == "nifty100":
                raise RuntimeError("db connection reset")
            return {"available": True, "beat_benchmark_pct": 45.5, "buy_hit_rate_pct": 50.3,
                    "buy_signals": 3518, "run_at": "t"}

        with patch("services.validation_engine.get_latest_results", side_effect=fake_results):
            result = get_track_record_summary("IN", "medium")

        assert len(result) == 1
        assert result[0]["universe"] == "midcap"

    def test_unknown_market_returns_empty_list_not_an_error(self):
        from services.validation_engine import get_track_record_summary
        assert get_track_record_summary("CRYPTO", "short") == []


# ─── GET /api/picks/daily enrichment ────────────────────────────────────────

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


@pytest.mark.regression
class TestDailyPicksEndpointTrackRecord:
    def _fake_cached_picks(self):
        return {
            "generated_at": "2026-07-17T00:24:46Z",
            "market": "IN",
            "picks": {"short": [], "medium": [{"symbol": "BSE", "confidence": 68}], "long": []},
        }

    def test_response_includes_historical_track_record_key(self, client):
        with patch("services.daily_picks.get_cached_picks", return_value=self._fake_cached_picks()), \
             patch("services.daily_picks._generating", {}), \
             patch("services.validation_engine.get_track_record_summary",
                   return_value=[{"universe": "nifty100", "beat_benchmark_pct": 49.4,
                                  "buy_hit_rate_pct": 55.3, "n_signals": 4995, "run_at": "t"}]):
            resp = client.get("/api/picks/daily", params={"market": "IN"})
        body = resp.json()
        assert "historical_track_record" in body
        assert body["historical_track_record"]["medium"][0]["universe"] == "nifty100"
        assert "short" in body["historical_track_record"]
        assert "long" in body["historical_track_record"]

    def test_existing_pick_fields_are_unchanged_by_the_enrichment(self, client):
        """The whole point is additive-only — every existing field/value on
        the payload must survive byte-for-byte identical."""
        fake = self._fake_cached_picks()
        with patch("services.daily_picks.get_cached_picks", return_value=fake), \
             patch("services.daily_picks._generating", {}), \
             patch("services.validation_engine.get_track_record_summary", return_value=[]):
            resp = client.get("/api/picks/daily", params={"market": "IN"})
        body = resp.json()
        assert body["generated_at"] == fake["generated_at"]
        assert body["picks"] == fake["picks"]
        assert body["picks"]["medium"][0]["confidence"] == 68  # untouched

    def test_track_record_lookup_failure_does_not_break_the_picks_response(self, client):
        """A broken validation-engine lookup must never turn a working picks
        response into a 500 — this is a nice-to-have enrichment, not a
        required dependency."""
        with patch("services.daily_picks.get_cached_picks", return_value=self._fake_cached_picks()), \
             patch("services.daily_picks._generating", {}), \
             patch("services.validation_engine.get_track_record_summary",
                   side_effect=RuntimeError("db down")):
            resp = client.get("/api/picks/daily", params={"market": "IN"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["picks"] == self._fake_cached_picks()["picks"]
        assert body["historical_track_record"] == {}

    def test_no_picks_yet_response_still_includes_empty_track_record_key(self, client):
        with patch("services.daily_picks.get_cached_picks", return_value=None), \
             patch("services.daily_picks._generating", {}):
            resp = client.get("/api/picks/daily", params={"market": "US"})
        body = resp.json()
        assert body["historical_track_record"] == {}


# ─── SELL/HOLD confidence investigation (2026-07-17) ───────────────────────
# Extends the BUY-only recalibration from commit f068156. Queried real
# persisted validation data before touching anything: SELL had the exact
# same range-mismatch bug as BUY (132,354 signals, composite scores
# essentially never below ~25 against an assumed [0,45) range) — fixed.
# HOLD's flat hit rate across its entire range (600,121 signals, no peak
# near the assumed midpoint of 52) means its formula's whole premise isn't
# supported by data, not just mis-scaled — deliberately left unchanged
# rather than fabricating a fix.

@pytest.mark.regression
class TestSellHoldConfidenceInvestigation:
    def _conf(self, composite, signal):
        """Mirrors prediction_engine.py's three-branch formula exactly —
        used here only to assert the intended post-fix behavior."""
        if signal == "BUY":
            return round(max(0, min(100, (composite - 60) / 20 * 100)))
        if signal == "SELL":
            return round(max(0, min(100, (45 - composite) / 20 * 100)))
        return max(0, min(100, 50 - int(abs(composite - 52) * 2)))

    def test_sell_confidence_rescaled_over_observed_25_to_45_range(self):
        assert self._conf(45, "SELL") == 0
        assert self._conf(25, "SELL") == 100
        assert self._conf(35, "SELL") == 50  # midpoint of the real range

    def test_sell_confidence_matches_production_and_validation_formulas(self):
        import services.prediction_engine as pe
        import services.validation_engine as ve
        src_pe = __import__("inspect").getsource(pe.PredictionEngine._composite_signal)
        src_ve = __import__("inspect").getsource(ve._confidence_from_composite)
        assert "(45 - composite_r) / 20 * 100" in src_pe
        assert "(45 - composite_r) / 20 * 100" in src_ve

    def test_hold_confidence_formula_deliberately_unchanged(self):
        """Locks in that HOLD was investigated and NOT rescaled — a future
        edit changing this without re-querying real hit-rate-by-bucket data
        first would be repeating the exact mistake this session avoided."""
        import services.prediction_engine as pe
        import services.validation_engine as ve
        src_pe = __import__("inspect").getsource(pe.PredictionEngine._composite_signal)
        src_ve = __import__("inspect").getsource(ve._confidence_from_composite)
        assert "50 - int(abs(composite_r - 52) * 2)" in src_pe
        assert "50 - int(abs(composite_r - 52) * 2)" in src_ve


@pytest.mark.regression
class TestSellConfidenceBuckets:
    def test_sell_confidence_buckets_key_present_in_metrics(self):
        from services.validation_engine import _compute_metrics
        signals = [
            {"predicted": "SELL", "composite_score": 30, "confidence": 60,
             "fwd_return_pct": 1.0, "alpha_pct": 0.5, "correct": 1,
             "tech_score": 40, "rs_score": 40, "obv_score": 40, "mfi_score": 40},
            {"predicted": "SELL", "composite_score": 40, "confidence": 25,
             "fwd_return_pct": -0.5, "alpha_pct": -0.2, "correct": 0,
             "tech_score": 40, "rs_score": 40, "obv_score": 40, "mfi_score": 40},
        ]
        metrics = _compute_metrics(signals, benchmark_return_pct=0.2, horizon="medium")
        assert "sell_confidence_buckets" in metrics
        ranges = {b["confidence_range"] for b in metrics["sell_confidence_buckets"]}
        assert "25–50" in ranges
        assert "50–75" in ranges
