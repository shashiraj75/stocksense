"""
DP-025 hardening follow-up (2026-07-18) — behavioural tests for:
  - accurate stage-coverage metadata (stages_supplied_not_replayed /
    stages_actually_replayed / full_pipeline_replay / production_equivalent);
  - truthful, structured production_provenance (invoked/not_invoked);
  - new contract-validation rules in contracts.validate_structure().

Does not rely solely on source-text assertions — every test drives
replay_snapshot() or validate_structure() and asserts on structured output.
"""

import math

import pytest

from services.validation.contracts import (
    MarketSnapshot, CandidateSnapshot, ReplayMode, ReplayStatus, PointInTimeIntegrity,
    STAGES_SUPPLIED_NOT_REPLAYED, STAGES_REPLAYABLE_ON_COMPLETION,
    validate_structure,
)
from services.validation.pipeline_replay import replay_snapshot


IC_WEIGHTS = {"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}


def _candidate(symbol, confidence=70, tech=60.0, fund=55.0, sentiment=50.0, quality=52.0,
                sentiment_available=True, quality_available=True, signal="BUY",
                reasoning=None, cap_tier="large"):
    return CandidateSnapshot(
        symbol=symbol, signal=signal, confidence=confidence,
        technical_score=tech, fundamental_score=fund,
        sentiment_score=sentiment if sentiment_available else None,
        sentiment_available=sentiment_available,
        quality_score=quality if quality_available else None,
        quality_available=quality_available,
        reasoning=reasoning or [], cap_tier=cap_tier,
    )


def _full_integrity():
    return {
        "provenance": PointInTimeIntegrity.POINT_IN_TIME,
        "timestamp": PointInTimeIntegrity.POINT_IN_TIME,
        "technical": PointInTimeIntegrity.POINT_IN_TIME,
        "fundamental": PointInTimeIntegrity.POINT_IN_TIME,
        "quality": PointInTimeIntegrity.POINT_IN_TIME,
    }


def _snapshot(candidates, horizon="medium", market="US", mode=ReplayMode.DIAGNOSTIC,
              regime_label="BULL_CALM", regime_id=0, source="manual_fixture",
              as_of="2026-01-01T00:00:00Z", point_in_time_integrity=None,
              returns_by_symbol=None):
    return MarketSnapshot(
        snapshot_id="snap-hardening", as_of=as_of, market=market, horizon=horizon,
        source=source, replay_mode=mode, candidates=candidates,
        regime_id=regime_id, regime_label=regime_label, regime_weight_multipliers={},
        ic_weights=IC_WEIGHTS,
        point_in_time_integrity=point_in_time_integrity if point_in_time_integrity is not None else _full_integrity(),
        returns_by_symbol=returns_by_symbol,
    )


# ---------------------------------------------------------------------------
# 1-6. Stage-coverage contract accuracy
# ---------------------------------------------------------------------------

class TestStageCoverageContract:
    def test_stages_supplied_not_replayed_is_the_fixed_catalog(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        assert set(result.stages_supplied_not_replayed) == set(STAGES_SUPPLIED_NOT_REPLAYED)

    def test_composite_and_confidence_stages_listed_as_supplied_not_replayed(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        joined = " ".join(result.stages_supplied_not_replayed)
        assert "prediction_engine_composite_calculation" in joined
        assert "signal_generation" in joined
        assert "confidence_generation_and_adjustments" in joined
        assert "target_price_calculation" in joined
        assert "stop_loss_trade_level_calculation" in joined

    def test_ranking_and_selection_stages_listed_as_actually_replayed_on_completion(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        assert result.status in (ReplayStatus.COMPLETED, ReplayStatus.DIAGNOSTIC_COMPLETED)
        assert "cross_sectional_zscore_ranking" in result.stages_actually_replayed
        assert "quality_eligibility" in result.stages_actually_replayed
        assert "issuer_deduplication" in result.stages_actually_replayed
        assert "final_horizon_selection" in result.stages_actually_replayed
        assert "portfolio_allocation" in result.stages_actually_replayed
        assert "cash_unallocated_calculation" in result.stages_actually_replayed
        assert set(result.stages_actually_replayed) == set(STAGES_REPLAYABLE_ON_COMPLETION)

    def test_refused_result_has_empty_actually_replayed_stages(self):
        snapshot = _snapshot([_candidate("A")], mode=ReplayMode.STRICT_POINT_IN_TIME, source="")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert result.stages_actually_replayed == []
        # supplied-not-replayed catalog is still reported even on refusal —
        # it describes the foundation's fixed scope, not this call's outcome
        assert set(result.stages_supplied_not_replayed) == set(STAGES_SUPPLIED_NOT_REPLAYED)


# ---------------------------------------------------------------------------
# 2-4. full_pipeline_replay / production_equivalent always False
# ---------------------------------------------------------------------------

class TestFullPipelineAndProductionEquivalentAlwaysFalse:
    def test_diagnostic_completion_both_false(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")], mode=ReplayMode.DIAGNOSTIC))
        assert result.full_pipeline_replay is False
        assert result.production_equivalent is False

    def test_strict_completion_both_still_false(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")], mode=ReplayMode.STRICT_POINT_IN_TIME))
        assert result.status == ReplayStatus.COMPLETED
        assert result.full_pipeline_replay is False
        assert result.production_equivalent is False

    def test_refused_result_both_false(self):
        result = replay_snapshot(_snapshot([_candidate("A")], mode=ReplayMode.STRICT_POINT_IN_TIME, source=""))
        assert result.full_pipeline_replay is False
        assert result.production_equivalent is False

    def test_strict_mode_cannot_flip_flags_true_even_with_fully_declared_integrity(self):
        # Exhaustively "clean" snapshot — every declared-integrity box
        # checked — still must not claim full/equivalent replay.
        integrity = _full_integrity()
        snapshot = _snapshot(
            [_candidate("A"), _candidate("B"), _candidate("C")],
            mode=ReplayMode.STRICT_POINT_IN_TIME, point_in_time_integrity=integrity,
        )
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.COMPLETED
        assert result.full_pipeline_replay is False
        assert result.production_equivalent is False


# ---------------------------------------------------------------------------
# 7-10. Truthful, per-result production provenance
# ---------------------------------------------------------------------------

class TestProductionProvenanceAccuracy:
    def test_zero_candidate_provenance_never_claims_optimizer_ran(self):
        result = replay_snapshot(_snapshot([]))
        assert "optimizer" not in result.production_provenance["invoked"]
        assert "optimizer" in result.production_provenance["not_invoked"]

    def test_one_candidate_provenance_never_claims_optimizer_ran(self):
        result = replay_snapshot(_snapshot([_candidate("A")]))
        assert len(result.selected_slate) == 1
        assert "optimizer" not in result.production_provenance["invoked"]
        assert "optimizer" in result.production_provenance["not_invoked"]

    def test_two_candidate_provenance_includes_optimizer_invocation(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        assert len(result.selected_slate) == 2
        assert result.production_provenance["invoked"].get("optimizer") == "services.alpha_engine.optimizer.optimize"
        assert "optimizer" not in result.production_provenance["not_invoked"]

    def test_invoked_functions_always_include_core_pipeline_stages(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        invoked = result.production_provenance["invoked"]
        assert invoked["ranking"] == "services.daily_picks._zscore_and_rank"
        assert invoked["quality_gate"] == "services.daily_picks._passes_quality_gate"
        assert invoked["issuer_dedup"] == "services.daily_picks._deduplicate_by_issuer"
        assert invoked["portfolio_allocation"] == "services.daily_picks._compute_portfolio_allocation"

    def test_short_horizon_selection_provenance_is_the_short_term_function(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")], horizon="short"))
        assert result.production_provenance["invoked"]["selection"] == "services.daily_picks._select_short_term_top_six"

    def test_medium_horizon_selection_provenance_is_the_tier_quota_function(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")], horizon="medium"))
        assert result.production_provenance["invoked"]["selection"] == "services.daily_picks._select_with_tier_quota"

    def test_composite_and_prediction_engine_never_appear_under_invoked(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        invoked_values = " ".join(result.production_provenance["invoked"].values())
        assert "PredictionEngine" not in invoked_values
        assert "_predict_stock" not in invoked_values

    def test_composite_and_prediction_engine_appear_under_not_invoked(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        not_invoked = result.production_provenance["not_invoked"]
        assert "prediction_engine_composite_calculation" in not_invoked
        assert "PredictionEngine" in not_invoked["prediction_engine_composite_calculation"]

    def test_refused_result_has_empty_invoked_and_full_not_invoked(self):
        result = replay_snapshot(_snapshot([_candidate("A")], mode=ReplayMode.STRICT_POINT_IN_TIME, source=""))
        assert result.production_provenance["invoked"] == {}
        assert len(result.production_provenance["not_invoked"]) > 0


# ---------------------------------------------------------------------------
# 11-15. New structural validation rules — refusals
# ---------------------------------------------------------------------------

class TestStructuralValidationRefusals:
    def test_invalid_market_refuses(self):
        snapshot = _snapshot([_candidate("A"), _candidate("B")], market="UK")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("market" in m for m in result.missing_requirements)

    def test_invalid_horizon_refuses(self):
        snapshot = _snapshot([_candidate("A"), _candidate("B")], horizon="weekly")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("horizon" in m for m in result.missing_requirements)

    def test_regime_id_not_in_production_map_refuses(self):
        snapshot = _snapshot([_candidate("A"), _candidate("B")], regime_id=99, regime_label="BULL_CALM")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("regime_id" in m for m in result.missing_requirements)

    def test_mismatched_regime_label_for_valid_id_refuses(self):
        # regime_id=0 is really BULL_CALM in production — declaring it as
        # BEAR_PANIC is a contradiction.
        snapshot = _snapshot([_candidate("A"), _candidate("B")], regime_id=0, regime_label="BEAR_PANIC")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("regime_label" in m for m in result.missing_requirements)

    def test_malformed_timestamp_refuses(self):
        snapshot = _snapshot([_candidate("A"), _candidate("B")], as_of="not-a-real-timestamp")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("as_of" in m for m in result.missing_requirements)

    def test_duplicate_symbols_refuse(self):
        snapshot = _snapshot([_candidate("DUP"), _candidate("DUP")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("duplicate" in m.lower() for m in result.missing_requirements)

    def test_empty_symbol_refuses(self):
        bad = _candidate("A")
        bad.symbol = ""
        snapshot = _snapshot([bad, _candidate("B")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_structural_refusal_applies_in_diagnostic_mode_too(self):
        # "Malformed structural values that make replay unsafe should
        # still refuse" even in DIAGNOSTIC — this is not a point-in-time
        # integrity gap, it's a well-formedness violation.
        snapshot = _snapshot([_candidate("A"), _candidate("B")], market="XX", mode=ReplayMode.DIAGNOSTIC)
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE


# ---------------------------------------------------------------------------
# 16-17. Sentiment/quality availability-vs-score contradiction
# ---------------------------------------------------------------------------

class TestSentimentQualityContradictionRules:
    def test_sentiment_unavailable_with_nonnull_score_refuses(self):
        contradictory = CandidateSnapshot(
            symbol="BAD", signal="BUY", confidence=70, technical_score=60, fundamental_score=55,
            quality_score=52, quality_available=True,
            sentiment_score=88.0, sentiment_available=False,  # contradiction
        )
        snapshot = _snapshot([contradictory, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("sentiment_available" in m for m in result.missing_requirements)

    def test_sentiment_available_with_null_score_refuses(self):
        contradictory = CandidateSnapshot(
            symbol="BAD", signal="BUY", confidence=70, technical_score=60, fundamental_score=55,
            quality_score=52, quality_available=True,
            sentiment_score=None, sentiment_available=True,  # contradiction
        )
        snapshot = _snapshot([contradictory, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_quality_unavailable_with_nonnull_score_refuses(self):
        contradictory = CandidateSnapshot(
            symbol="BAD", signal="BUY", confidence=70, technical_score=60, fundamental_score=55,
            quality_score=52.0, quality_available=False,  # contradiction — production's
            # contract derives quality_available strictly from raw-score-not-None
            sentiment_score=50.0, sentiment_available=True,
        )
        snapshot = _snapshot([contradictory, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_available" in m for m in result.missing_requirements)

    def test_quality_available_with_null_score_refuses(self):
        contradictory = CandidateSnapshot(
            symbol="BAD", signal="BUY", confidence=70, technical_score=60, fundamental_score=55,
            quality_score=None, quality_available=True,  # contradiction
            sentiment_score=50.0, sentiment_available=True,
        )
        snapshot = _snapshot([contradictory, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_genuine_zero_quality_score_with_available_true_is_not_a_contradiction(self):
        # DP-009 sanity: a genuine 0 is not a contradiction and must not refuse.
        genuine_zero = _candidate("ZERO", quality=0.0, quality_available=True)
        snapshot = _snapshot([genuine_zero, _candidate("B")])
        result = replay_snapshot(snapshot)
        assert result.status != ReplayStatus.REFUSED_INCOMPLETE

    def test_legitimately_missing_sentiment_is_not_a_contradiction(self):
        missing = _candidate("MISSING", sentiment_available=False)
        snapshot = _snapshot([missing, _candidate("B")])
        result = replay_snapshot(snapshot)
        assert result.status != ReplayStatus.REFUSED_INCOMPLETE


# ---------------------------------------------------------------------------
# 18. NaN / infinite scores refuse
# ---------------------------------------------------------------------------

class TestNonFiniteScoresRefuse:
    def test_nan_technical_score_refuses(self):
        bad = _candidate("BAD", tech=float("nan"))
        snapshot = _snapshot([bad, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("technical_score" in m for m in result.missing_requirements)

    def test_infinite_fundamental_score_refuses(self):
        bad = _candidate("BAD", fund=float("inf"))
        snapshot = _snapshot([bad, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_negative_infinite_confidence_refuses(self):
        bad = _candidate("BAD", confidence=float("-inf"))
        snapshot = _snapshot([bad, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_nan_quality_score_refuses(self):
        bad = _candidate("BAD", quality=float("nan"))
        snapshot = _snapshot([bad, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_nan_sentiment_score_refuses(self):
        bad = _candidate("BAD", sentiment=float("nan"), sentiment_available=True)
        snapshot = _snapshot([bad, _candidate("OK")])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_normal_finite_scores_do_not_refuse(self):
        snapshot = _snapshot([_candidate("A", tech=0.0, fund=100.0, quality=0.0), _candidate("B")])
        result = replay_snapshot(snapshot)
        assert result.status != ReplayStatus.REFUSED_INCOMPLETE


# ---------------------------------------------------------------------------
# 19. Malformed returns series — refuse (non-finite) or warn (length mismatch)
# ---------------------------------------------------------------------------

class TestReturnsSeriesValidation:
    def test_nan_in_returns_series_refuses(self):
        snapshot = _snapshot(
            [_candidate("A"), _candidate("B")],
            returns_by_symbol={"A": [0.01, float("nan"), 0.02], "B": [0.01, 0.02, 0.03]},
        )
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("returns_by_symbol" in m for m in result.missing_requirements)

    def test_infinite_value_in_returns_series_refuses(self):
        snapshot = _snapshot(
            [_candidate("A"), _candidate("B")],
            returns_by_symbol={"A": [0.01, 0.02], "B": [float("inf"), 0.02]},
        )
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_inconsistent_lengths_downgrades_with_structured_warning_not_refusal(self):
        snapshot = _snapshot(
            [_candidate("A"), _candidate("B")],
            returns_by_symbol={"A": [0.01, 0.02, 0.03], "B": [0.01, 0.02]},  # mismatched lengths
        )
        result = replay_snapshot(snapshot)
        assert result.status != ReplayStatus.REFUSED_INCOMPLETE
        assert any("inconsistent series lengths" in w for w in result.warnings)
        # optimizer still ran (2 candidates) but with no real returns matrix
        assert result.production_provenance["invoked"].get("optimizer") == "services.alpha_engine.optimizer.optimize"

    def test_well_formed_returns_series_produces_no_warning(self):
        snapshot = _snapshot(
            [_candidate("A"), _candidate("B")],
            returns_by_symbol={"A": [0.01, 0.02, 0.03] * 15, "B": [0.02, 0.01, -0.01] * 15},
        )
        result = replay_snapshot(snapshot)
        assert not any("inconsistent series lengths" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 20. Deterministic hashing stable with new metadata fields
# ---------------------------------------------------------------------------

class TestDeterminismWithNewFields:
    def test_repeated_replay_with_new_fields_still_hashes_identically(self):
        candidates = [_candidate("A"), _candidate("B"), _candidate("C")]
        r1 = replay_snapshot(_snapshot(list(candidates)))
        r2 = replay_snapshot(_snapshot(list(candidates)))
        assert r1.result_hash == r2.result_hash
        assert r1.stages_actually_replayed == r2.stages_actually_replayed
        assert r1.production_provenance == r2.production_provenance

    def test_hash_changes_when_optimizer_involvement_changes(self):
        one = replay_snapshot(_snapshot([_candidate("A")]))
        two = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        assert one.result_hash != two.result_hash


# ---------------------------------------------------------------------------
# validate_structure() unit-level tests (direct, not just through replay)
# ---------------------------------------------------------------------------

class TestValidateStructureDirect:
    def test_valid_snapshot_returns_no_errors(self):
        snapshot = _snapshot([_candidate("A"), _candidate("B")])
        assert validate_structure(snapshot) == []

    def test_regime_zero_is_bull_calm_by_production_contract(self):
        snapshot = _snapshot([_candidate("A")], regime_id=0, regime_label="BULL_CALM")
        assert validate_structure(snapshot) == []

    def test_no_network_call_performed_during_validation(self, monkeypatch):
        def _raise(*a, **kw):
            raise AssertionError("validate_structure must never perform network I/O")
        monkeypatch.setattr("yfinance.download", _raise, raising=False)
        snapshot = _snapshot([_candidate("A"), _candidate("B")])
        validate_structure(snapshot)  # must not raise
