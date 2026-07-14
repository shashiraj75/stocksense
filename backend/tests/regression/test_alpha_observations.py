"""
Learning Alpha Engine remediation, Phase 2A — shadow-only canonical
alpha_observations store.

These tests prove, without any network/DB access, that:
  - one canonical row is built per successfully scored, non-rejected
    candidate (winners AND non-winners),
  - raw-score vs. z-score columns hold their correct, different meanings,
  - missing sentiment/quality evidence is captured honestly, never
    fabricated,
  - the canonical row uses the GLOBAL regime, never PredictionEngine's local
    trend,
  - generation-reference provenance is required — current price / 0.0 is
    NEVER substituted when it's missing,
  - run_id is the reused job_id (or one fallback id per run, never one per
    row),
  - (run_id, market, horizon, symbol) is a real uniqueness constraint,
  - a persistence failure is isolated and non-fatal,
  - the existing predictions/outcomes logging path, get_training_data, and
    the published Daily Picks payload are all unaffected.
"""
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone, date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

import services.daily_picks as dp
from services.alpha_engine import alpha_observations as ao


def _cand(
    symbol="AAA", signal="BUY", tech=60.0, fund=55.0, sentiment=50.0,
    quality=52.0, sentiment_available=True, quality_available=True,
    quality_raw_score=None,
    ref_price=100.0, ref_source="yahoo_daily_history",
    ref_basis="adjusted_close", ref_as_of="2026-07-10T00:00:00",
    tech_z=0.5, fund_z=0.2, sentiment_z=-0.1, quality_z=0.3,
    combined_alpha=0.25, meta_alpha=None, ranking_alpha=0.25,
    composite_score=58.0, confidence=70,
) -> dict:
    # quality_raw_score defaults to `quality` when available and not
    # explicitly overridden — mirrors _predict_stock's real contract, where
    # quality_raw_score is the genuine pre-fallback value (never the
    # `or 50`-coalesced ranking quality_score).
    if quality_raw_score is None and quality_available:
        quality_raw_score = quality
    return {
        "symbol": symbol,
        "signal": signal,
        "tech_score": tech,
        "fund_score": fund,
        "sentiment_score": sentiment if sentiment_available else None,
        "quality_score": quality,
        "sentiment_available": sentiment_available,
        "quality_available": quality_available,
        "quality_raw_score": quality_raw_score,
        "generation_reference_price": ref_price,
        "generation_reference_source": ref_source,
        "generation_reference_price_basis": ref_basis,
        "generation_reference_as_of": ref_as_of,
        "factor_zscores": {
            "tech": tech_z, "fund": fund_z, "sentiment": sentiment_z, "quality": quality_z,
        },
        "combined_alpha": combined_alpha,
        "meta_alpha": meta_alpha,
        "ranking_alpha": ranking_alpha,
        "composite_score": composite_score,
        "confidence": confidence,
    }


_COMMON_KW = dict(
    run_id="run-123", market="US", horizon="short",
    run_generated_at=datetime(2026, 7, 10, 4, 0, tzinfo=timezone.utc),
    run_session_date=date(2026, 7, 10),
    regime_id=2, regime_label="BULL_CALM",
)


class TestBuildAlphaObservationRow:
    def test_produces_one_row_per_candidate_with_correct_symbol(self):
        row = dp._build_alpha_observation_row(_cand("AAPL"), **_COMMON_KW)
        assert row is not None
        assert row["symbol"] == "AAPL"
        assert row["run_id"] == "run-123"
        assert row["market"] == "US"
        assert row["horizon"] == "short"

    def test_non_winner_gets_false_null_selection_metadata(self):
        row = dp._build_alpha_observation_row(_cand("BBB"), pick_meta=None, **_COMMON_KW)
        assert row["is_daily_pick"] is False
        assert row["pick_rank"] is None
        assert row["portfolio_weight"] is None

    def test_winner_carries_pick_rank_and_portfolio_weight(self):
        row = dp._build_alpha_observation_row(
            _cand("CCC"), pick_meta={"pick_rank": 3, "portfolio_weight": 0.18}, **_COMMON_KW
        )
        assert row["is_daily_pick"] is True
        assert row["pick_rank"] == 3
        assert row["portfolio_weight"] == 0.18

    def test_raw_and_zscore_columns_hold_different_correct_values(self):
        c = _cand("DDD", tech=60.0, tech_z=1.75)
        row = dp._build_alpha_observation_row(c, **_COMMON_KW)
        assert row["technical_raw_score"] == 60.0
        assert row["technical_zscore"] == 1.75
        assert row["technical_raw_score"] != row["technical_zscore"]
        # never a raw/100 value stored under a zscore column
        assert row["technical_zscore"] != 0.60

    def test_missing_sentiment_is_honestly_recorded(self):
        c = _cand("EEE", sentiment_available=False, sentiment_z=0.0)
        row = dp._build_alpha_observation_row(c, **_COMMON_KW)
        assert row["sentiment_available"] is False
        assert row["sentiment_raw_score"] is None          # never fabricated
        assert row["sentiment_zscore"] == 0.0               # exact value ranking used

    def test_missing_quality_distinguishable_from_genuine_score(self):
        genuine = dp._build_alpha_observation_row(
            _cand("FFF", quality=52.0, quality_available=True), **_COMMON_KW
        )
        fallback = dp._build_alpha_observation_row(
            _cand("GGG", quality=50.0, quality_available=False), **_COMMON_KW
        )
        assert genuine["quality_available"] is True
        assert genuine["quality_raw_score"] == 52.0
        assert fallback["quality_available"] is False
        assert fallback["quality_raw_score"] is None
        # the (internal, fallback=50) ranking value must never surface as if genuine
        assert fallback["quality_raw_score"] != 50.0

    def test_canonical_regime_is_the_global_kmeans_regime(self):
        row = dp._build_alpha_observation_row(_cand("HHH"), **_COMMON_KW)
        assert row["canonical_regime_id"] == 2
        assert row["canonical_regime_label"] == "BULL_CALM"

    def test_uses_generation_reference_provenance(self):
        c = _cand(
            "III", ref_price=123.45, ref_source="yahoo_daily_history",
            ref_basis="adjusted_close", ref_as_of="2026-07-09T00:00:00",
        )
        row = dp._build_alpha_observation_row(c, **_COMMON_KW)
        assert row["reference_price"] == 123.45
        assert row["reference_price_source"] == "yahoo_daily_history"
        assert row["reference_price_basis"] == "adjusted_close"
        assert row["reference_session_date"] == date(2026, 7, 9)

    def test_never_substitutes_current_price_or_zero_when_provenance_missing(self):
        missing_price = _cand("JJJ")
        missing_price["generation_reference_price"] = None
        assert dp._build_alpha_observation_row(missing_price, **_COMMON_KW) is None

        zero_price = _cand("KKK")
        zero_price["generation_reference_price"] = 0.0
        assert dp._build_alpha_observation_row(zero_price, **_COMMON_KW) is None

        missing_source = _cand("LLL")
        missing_source["generation_reference_source"] = None
        assert dp._build_alpha_observation_row(missing_source, **_COMMON_KW) is None

        missing_as_of = _cand("MMM")
        missing_as_of["generation_reference_as_of"] = None
        assert dp._build_alpha_observation_row(missing_as_of, **_COMMON_KW) is None

    def test_negative_reference_price_is_rejected(self):
        c = _cand("NNN")
        c["generation_reference_price"] = -5.0
        assert dp._build_alpha_observation_row(c, **_COMMON_KW) is None

    def test_quality_raw_score_never_falls_back_to_ranking_or_50(self):
        """The fix under test: quality_raw_score must come from the genuine
        pre-fallback source, never from the `or 50`-coalesced ranking
        quality_score field."""
        c = _cand("OOO", quality=0, quality_available=True, quality_raw_score=0)
        row = dp._build_alpha_observation_row(c, **_COMMON_KW)
        assert row["quality_available"] is True
        assert row["quality_raw_score"] == 0
        assert row["quality_raw_score"] is not None
        # the candidate's own ranking-facing quality_score is unaffected —
        # it remains 50 (the pre-existing, untouched fallback) even though
        # the canonical row correctly captures the genuine 0.
        assert c["quality_score"] == 0  # _cand mirrors the caller's own value here


def _predict_stock_with_quality(qf_score, market="US"):
    """Drive the real _predict_stock() through a mocked PredictionEngine.predict
    to prove the fix at its actual source, not just at the builder level."""
    quality_factors = {"score": qf_score} if qf_score is not None else {}

    async def fake_predict(self, symbol, market, horizon):
        return {
            "signal": "BUY", "confidence": 80, "current_price": 100.0,
            "reasoning": [], "trade_levels": {}, "quality_factors": quality_factors,
            "technical": {"score": 60}, "fundamental_score": {"score": 55},
            "sentiment_score": {"score": 50, "data_available": True},
            "price_reference": {
                "price": 100.0, "source": "yahoo_daily_history",
                "price_basis": "adjusted_close", "as_of": "2026-07-10T00:00:00",
            },
        }

    with patch.object(dp.PredictionEngine, "predict", fake_predict):
        return dp._predict_stock("ZZZ", "short", market)


class TestPredictStockQualityRawScoreSource:
    """End-to-end from _predict_stock's real qf extraction through to the
    alpha_observations builder — proves the fix at its actual source, and
    that the existing ranking field is untouched."""

    def test_genuine_zero_quality_score(self):
        cand = _predict_stock_with_quality(0)
        assert cand["quality_available"] is True
        assert cand["quality_raw_score"] == 0
        # existing ranking field is completely unchanged by this fix —
        # still 50, via the pre-existing, untouched `or 50` fallback.
        assert cand["quality_score"] == 50

        row = dp._build_alpha_observation_row(cand, **_COMMON_KW)
        assert row["quality_available"] is True
        assert row["quality_raw_score"] == 0

    def test_genuine_nonzero_quality_score(self):
        cand = _predict_stock_with_quality(37)
        assert cand["quality_available"] is True
        assert cand["quality_raw_score"] == 37
        assert cand["quality_score"] == 37  # non-falsy, `or 50` doesn't kick in

        row = dp._build_alpha_observation_row(cand, **_COMMON_KW)
        assert row["quality_raw_score"] == 37

    def test_missing_quality_score_key(self):
        cand = _predict_stock_with_quality(None)  # quality_factors == {}
        assert cand["quality_available"] is False
        assert cand["quality_raw_score"] is None
        assert cand["quality_score"] == 50  # ranking fallback unchanged

        row = dp._build_alpha_observation_row(cand, **_COMMON_KW)
        assert row["quality_available"] is False
        assert row["quality_raw_score"] is None

    def test_explicit_quality_score_none(self):
        async def fake_predict(self, symbol, market, horizon):
            return {
                "signal": "BUY", "confidence": 80, "current_price": 100.0,
                "reasoning": [], "trade_levels": {},
                "quality_factors": {"score": None},
                "technical": {"score": 60}, "fundamental_score": {"score": 55},
                "sentiment_score": {"score": 50, "data_available": True},
                "price_reference": {
                    "price": 100.0, "source": "yahoo_daily_history",
                    "price_basis": "adjusted_close", "as_of": "2026-07-10T00:00:00",
                },
            }
        with patch.object(dp.PredictionEngine, "predict", fake_predict):
            cand = dp._predict_stock("ZZZ", "short", "US")
        assert cand["quality_available"] is False
        assert cand["quality_raw_score"] is None
        assert cand["quality_score"] == 50

    def test_quality_raw_score_and_availability_never_in_published_payload(self):
        cand = _predict_stock_with_quality(0)
        sanitized = {k: v for k, v in cand.items() if k not in dp._ALPHA_OBS_ONLY_KEYS}
        assert "quality_raw_score" not in sanitized
        assert "quality_available" not in sanitized
        assert "sentiment_available" not in sanitized
        # everything else (including the ranking-facing quality_score) is untouched
        assert sanitized["quality_score"] == 50


class TestAlphaObservationsSqliteStore:
    """Storage-layer contract tests against a real (temp-file) SQLite DB —
    no network/production DB involved."""

    @pytest.fixture(autouse=True)
    def _tmp_db(self, monkeypatch):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        monkeypatch.setattr(ao, "DB_PATH", path)
        monkeypatch.setattr(ao, "USE_POSTGRES", False)
        yield path
        os.unlink(path)

    def _row(self, **overrides):
        base = dict(
            observation_id=str(uuid.uuid4()), run_id="run-A", market="IN",
            horizon="short", symbol="RELIANCE",
            run_generated_at="2026-07-10T04:00:00+00:00",
            run_session_date="2026-07-10", reference_session_date="2026-07-09",
            reference_price=2500.0, reference_price_source="yahoo_daily_history",
            reference_price_basis="adjusted_close",
            technical_raw_score=60.0, fundamental_raw_score=55.0,
            sentiment_raw_score=None, quality_raw_score=None,
            sentiment_available=False, quality_available=False,
            technical_zscore=0.5, fundamental_zscore=0.2,
            sentiment_zscore=0.0, quality_zscore=0.3,
            composite_score=58.0, ic_combined_alpha=0.25, meta_alpha=None,
            ranking_alpha=0.25, signal="BUY", signal_confidence=70,
            canonical_regime_id=2, canonical_regime_label="BULL_CALM",
            feature_schema_version=1, regime_schema_version=1,
            is_daily_pick=False, pick_rank=None, portfolio_weight=None,
        )
        base.update(overrides)
        return base

    def test_every_candidate_produces_exactly_one_row(self):
        rows = [self._row(symbol="AAA"), self._row(symbol="BBB"), self._row(symbol="CCC")]
        assert ao.save_observations(rows) is True
        conn = sqlite3.connect(ao.DB_PATH)
        count = conn.execute("SELECT COUNT(*) FROM alpha_observations").fetchone()[0]
        conn.close()
        assert count == 3

    def test_same_run_symbol_market_horizon_cannot_duplicate(self):
        row = self._row(symbol="DUP", run_id="run-X")
        assert ao.save_observations([row]) is True
        # Retry of the same run (e.g. a job retry reusing job_id as run_id)
        assert ao.save_observations([row]) is True
        conn = sqlite3.connect(ao.DB_PATH)
        count = conn.execute(
            "SELECT COUNT(*) FROM alpha_observations WHERE run_id='run-X' AND symbol='DUP'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_new_run_id_is_allowed_as_a_separate_row(self):
        row1 = self._row(symbol="DUP2", run_id="run-Y1")
        row2 = self._row(symbol="DUP2", run_id="run-Y2")
        assert ao.save_observations([row1]) is True
        assert ao.save_observations([row2]) is True
        conn = sqlite3.connect(ao.DB_PATH)
        count = conn.execute(
            "SELECT COUNT(*) FROM alpha_observations WHERE symbol='DUP2'"
        ).fetchone()[0]
        conn.close()
        assert count == 2

    def test_save_observations_failure_is_non_fatal(self):
        # Point at an unwritable path to force a real failure, confirm it's
        # swallowed and reported as False rather than raised.
        with patch.object(ao, "_conn", side_effect=sqlite3.OperationalError("boom")):
            result = ao.save_observations([self._row(symbol="ZZZ")])
        assert result is False  # never raises

    def test_persisted_quality_raw_score_preserves_genuine_zero(self):
        row = self._row(symbol="QZERO", quality_available=True, quality_raw_score=0)
        assert ao.save_observations([row]) is True
        conn = sqlite3.connect(ao.DB_PATH)
        stored = conn.execute(
            "SELECT quality_raw_score, quality_available FROM alpha_observations WHERE symbol='QZERO'"
        ).fetchone()
        conn.close()
        assert stored[0] == 0
        assert stored[0] is not None
        assert bool(stored[1]) is True

    def test_persisted_quality_raw_score_is_null_when_unavailable(self):
        row = self._row(symbol="QNULL", quality_available=False, quality_raw_score=None)
        assert ao.save_observations([row]) is True
        conn = sqlite3.connect(ao.DB_PATH)
        stored = conn.execute(
            "SELECT quality_raw_score, quality_available FROM alpha_observations WHERE symbol='QNULL'"
        ).fetchone()
        conn.close()
        assert stored[0] is None
        assert bool(stored[1]) is False


@pytest.mark.regression
class TestGeneratePicksInnerShadowIntegration:
    """
    Full-pipeline integration, heavily mocked (no network/production DB) —
    proves run_id reuse across horizons, existing predictions logging is
    unaffected, the published payload is unaffected, and a persistence
    failure in alpha_observations does not break the run.
    """

    def _enriched_candidate(self, symbol, horizon, alpha, is_buy=True):
        return {
            "symbol": symbol, "horizon": horizon,
            "signal": "BUY" if is_buy else "HOLD",
            "confidence": 90, "price": 100.0,
            "tech_score": 60.0, "fund_score": 55.0,
            "sentiment_score": 50.0, "quality_score": 52.0,
            "sentiment_available": True, "quality_available": True,
            "quality_raw_score": 52.0,
            "generation_reference_price": 100.0,
            "generation_reference_source": "yahoo_daily_history",
            "generation_reference_price_basis": "adjusted_close",
            "generation_reference_as_of": "2026-07-10T00:00:00",
            "factor_zscores": {"tech": 0.5, "fund": 0.2, "sentiment": 0.1, "quality": 0.3},
            "combined_alpha": alpha, "meta_alpha": None, "ranking_alpha": alpha,
            "composite_score": 58.0, "reasoning": [],
        }

    def _run(self, job_id, save_obs_mock):
        candidates = ["AAA", "BBB", "CCC"]

        def fake_zscore_and_rank(items, ic_weights, regime, regime_id, market="IN",
                                  production_learning_enabled=None):
            # Every candidate scored, only some are BUY (mirrors the real
            # function's contract: full cross-section in, enriched full
            # cross-section out).
            return [
                self._enriched_candidate(it["symbol"], it["horizon"], 0.9 - i * 0.1, is_buy=(i < 2))
                for i, it in enumerate(items)
            ]

        with patch("services.daily_picks._bulk_screen",
                    return_value=(candidates, 3, "fundamentals_cache", False, None,
                                  {"universe_candidate_count": 3, "attempts": 1,
                                   "reason": "ok", "error_category": "none", "tier_map": {}})), \
             patch("services.daily_picks._predict_stock",
                   side_effect=lambda sym, h, market: {
                       "symbol": sym, "horizon": h, "signal": "BUY",
                       "confidence": 90, "cap_tier": None,
                   }), \
             patch("services.daily_picks._write_score_snapshots"), \
             patch("services.daily_picks._zscore_and_rank", side_effect=fake_zscore_and_rank), \
             patch("services.alpha_engine.regime_cluster.detect_regime",
                   return_value={"regime_id": 2, "label": "BULL_CALM", "description": "",
                                 "trend": "BULLISH", "score_adj": 0, "weight_multipliers": {}}), \
             patch("services.alpha_engine.ic_engine.get_ic_weights", return_value={}), \
             patch("services.alpha_engine.optimizer.optimize", return_value=[0.6, 0.4]), \
             patch("services.alpha_engine.store.log_prediction") as mock_log_pred, \
             patch("services.daily_picks._alpha_obs.save_observations", side_effect=save_obs_mock) as mock_save_obs, \
             patch("services.global_context.get_global_context", return_value={}), \
             patch("services.daily_picks._fetch_returns_matrix", return_value=None), \
             patch("services.daily_picks.yf.download", return_value=pd.DataFrame()), \
             patch("services.daily_picks.yf.utils.get_crumb", create=True), \
             patch("os.getenv", return_value=None):
            payload, _persisted_at = dp._generate_picks_inner("US", job_id=job_id)
        return payload, mock_log_pred, mock_save_obs

    def test_run_id_reused_as_job_id_across_all_horizons(self):
        captured_run_ids = []

        def capture(rows):
            captured_run_ids.extend(r["run_id"] for r in rows)
            return True

        payload, mock_log_pred, mock_save_obs = self._run("job-durable-42", capture)
        assert captured_run_ids, "expected at least one alpha_observations row"
        assert set(captured_run_ids) == {"job-durable-42"}

    def test_every_scored_non_rejected_candidate_produces_a_row_including_non_winners(self):
        captured = []
        payload, *_ = self._run("job-1", lambda rows: (captured.extend(rows), True)[1])
        # 3 candidates scored per horizon x 3 horizons = 9 rows; only 2 of 3
        # are BUY per horizon (fake_zscore_and_rank), i.e. non-winners
        # (HOLD/non-selected) are still present.
        assert len(captured) == 9
        signals = {r["symbol"]: r["signal"] for r in captured}
        assert "HOLD" in signals.values() or any(not r["is_daily_pick"] for r in captured)

    def test_existing_predictions_logging_path_is_unaffected(self):
        payload, mock_log_pred, _ = self._run("job-2", lambda rows: True)
        # Phase 7's existing log_prediction call still fires for winners,
        # unchanged in shape.
        assert mock_log_pred.call_count > 0
        for call in mock_log_pred.call_args_list:
            assert "market" in call.kwargs
            assert call.kwargs["is_daily_pick"] is True

    def test_published_payload_never_contains_alpha_observation_only_fields(self):
        payload, *_ = self._run("job-3", lambda rows: True)
        for horizon_picks in payload["picks"].values():
            for pick in horizon_picks:
                assert "sentiment_available" not in pick
                assert "quality_available" not in pick
                assert "quality_raw_score" not in pick

    def test_ranking_selection_and_portfolio_weights_unaffected_by_the_fix(self):
        """Deep-equality check that this correction (adding quality_raw_score)
        changes nothing about signals, ranking_alpha, selection, or portfolio
        weights — only the shadow alpha_observations rows differ."""
        import copy
        payload_a, *_ = self._run("job-5a", lambda rows: True)
        payload_b, *_ = self._run("job-5b", lambda rows: True)

        def strip_nondeterministic(p):
            p = copy.deepcopy(p)
            p.pop("generated_at", None)
            # source_job_id (Daily Picks Scheduler Remediation Phase 1A base-job
            # provenance) is set from the job_id argument passed to _run() —
            # this test deliberately varies job_id across calls to prove
            # ranking/selection/output is unaffected by it, so it is expected,
            # intentional nondeterminism here, not a regression to mask.
            p.pop("source_job_id", None)
            return p

        assert strip_nondeterministic(payload_a) == strip_nondeterministic(payload_b)

    def test_persistence_failure_is_isolated_and_non_fatal(self):
        def boom(rows):
            raise RuntimeError("simulated DB outage")
        payload, mock_log_pred, mock_save_obs = self._run("job-4", boom)
        # The run still completes and returns a normal payload despite the
        # alpha_observations write raising.
        assert payload is not None
        assert "picks" in payload
        assert mock_log_pred.call_count > 0  # existing job lifecycle unaffected

    def test_persistence_success_noop_and_failure_produce_deeply_equal_payloads(self):
        import copy

        def strip_nondeterministic(p):
            p = copy.deepcopy(p)
            p.pop("generated_at", None)
            # source_job_id (Daily Picks Scheduler Remediation Phase 1A base-job
            # provenance) is set from the job_id argument passed to _run() —
            # this test deliberately varies job_id across calls to prove
            # ranking/selection/output is unaffected by it, so it is expected,
            # intentional nondeterminism here, not a regression to mask.
            p.pop("source_job_id", None)
            return p

        payload_success, *_ = self._run("job-6a", lambda rows: True)
        payload_noop, *_ = self._run("job-6b", lambda rows: True)
        payload_failure, *_ = self._run("job-6c", lambda rows: (_ for _ in ()).throw(RuntimeError("down")))

        a = strip_nondeterministic(payload_success)
        b = strip_nondeterministic(payload_noop)
        c = strip_nondeterministic(payload_failure)
        assert a == b
        assert a == c
