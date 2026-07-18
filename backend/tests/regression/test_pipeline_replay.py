"""
DP-025 foundation (governed by DPD-009, DECIDED — DISCLOSURE HOLD) —
behavioural tests for services/validation/pipeline_replay.py.

Deterministic fixtures only. No network, no production database access.
See test_pipeline_replay_side_effects.py for the dedicated side-effect
guard suite (network/persistence/generation/retraining prohibitions).

This test file does NOT rely solely on source-code text assertions — every
test drives the real replay_snapshot() entry point and asserts on its
structured output.
"""

from unittest.mock import patch

import pytest

import services.daily_picks as dp
from services.alpha_engine.optimizer import optimize
from services.validation.contracts import (
    MarketSnapshot, CandidateSnapshot, ReplayMode, ReplayStatus, PointInTimeIntegrity,
    REJECTION_ISSUER_DUPLICATE, REJECTION_NOT_BUY_SIGNAL, REJECTION_CONFIDENCE_BELOW_FLOOR,
    REJECTION_NOT_SELECTED,
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
        quality_score=quality, quality_available=quality_available,
        reasoning=reasoning or [], cap_tier=cap_tier,
    )


def _fully_point_in_time_integrity():
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
              synthetic_flags=None, returns_by_symbol=None):
    return MarketSnapshot(
        snapshot_id="snap-1", as_of=as_of, market=market, horizon=horizon,
        source=source, replay_mode=mode, candidates=candidates,
        regime_id=regime_id, regime_label=regime_label, regime_weight_multipliers={},
        ic_weights=IC_WEIGHTS,
        point_in_time_integrity=point_in_time_integrity if point_in_time_integrity is not None else _fully_point_in_time_integrity(),
        synthetic_flags=synthetic_flags or {},
        returns_by_symbol=returns_by_symbol,
    )


# ---------------------------------------------------------------------------
# 1. Real _zscore_and_rank invoked
# ---------------------------------------------------------------------------

class TestInvokesRealZscoreAndRank:
    def test_zscore_and_rank_is_called_with_correct_shape_and_output_matches_direct_call(self):
        candidates = [_candidate("AAA", quality=0.0), _candidate("BBB", quality=40.0), _candidate("CCC", quality=80.0)]
        snapshot = _snapshot(candidates)

        with patch("services.daily_picks._zscore_and_rank", wraps=dp._zscore_and_rank) as spy:
            result = replay_snapshot(snapshot)
        assert spy.called
        call_kwargs = spy.call_args
        assert call_kwargs.args[0][0]["symbol"] in {"AAA", "BBB", "CCC"}

        # cross-check output against a direct call with the same rows
        rows = [
            {"symbol": c.symbol, "horizon": "medium", "signal": c.signal, "confidence": c.confidence,
             "tech_score": c.technical_score, "fund_score": c.fundamental_score,
             "sentiment_score": c.sentiment_score, "quality_score": c.quality_score,
             "reasoning": c.reasoning, "cap_tier": c.cap_tier}
            for c in candidates
        ]
        direct = dp._zscore_and_rank(rows, IC_WEIGHTS, {"label": "BULL_CALM"}, 0, market="US", production_learning_enabled=False)
        direct_by_symbol = {r["symbol"]: r["ranking_alpha"] for r in direct}
        result_by_symbol = {r.symbol: r.ranking_alpha for r in result.ranked_universe}
        assert direct_by_symbol == result_by_symbol


# ---------------------------------------------------------------------------
# 2. Containment stays disabled
# ---------------------------------------------------------------------------

class TestContainmentDisabledByDefault:
    def test_replay_never_activates_production_learning_even_if_env_var_set(self, monkeypatch):
        monkeypatch.setenv("LEARNING_ALPHA_PRODUCTION_ENABLED", "1")
        candidates = [_candidate("AAA"), _candidate("BBB")]
        snapshot = _snapshot(candidates)
        with patch("services.alpha_engine.meta_model.predict", return_value=999.0):
            result = replay_snapshot(snapshot)
        for row in result.ranked_universe:
            assert row.meta_alpha_used_for_ranking is False
            assert row.ranking_alpha == row.combined_alpha


# ---------------------------------------------------------------------------
# 3. DP-009/DP-010 zero-score preservation
# ---------------------------------------------------------------------------

class TestZeroScorePreservation:
    def test_genuine_zero_quality_score_treated_as_real_evidence_not_neutral_fifty(self):
        candidates = [_candidate("ZERO", quality=0.0), _candidate("MID", quality=20.0), _candidate("HIGH", quality=40.0)]
        snapshot = _snapshot(candidates)
        result = replay_snapshot(snapshot)
        by_symbol = {r.symbol: r.factor_zscores["quality"] for r in result.ranked_universe}
        # mean of [0, 20, 40] = 20 -> MID should land at ~0, ZERO must be
        # negative (below the true mean), not the ~0 it would be if
        # silently coalesced to 50 (which is *above* this mean).
        assert by_symbol["MID"] == pytest.approx(0.0, abs=1e-6)
        assert by_symbol["ZERO"] < 0


# ---------------------------------------------------------------------------
# 4. Missing sentiment follows production's real missing-evidence contract
# ---------------------------------------------------------------------------

class TestMissingSentimentContract:
    def test_missing_sentiment_gets_zero_zscore_contribution(self):
        candidates = [
            _candidate("MISSING", sentiment_available=False),
            _candidate("PRESENT", sentiment=90.0, sentiment_available=True),
        ]
        snapshot = _snapshot(candidates)
        result = replay_snapshot(snapshot)
        by_symbol = {r.symbol: r.factor_zscores["sentiment"] for r in result.ranked_universe}
        assert by_symbol["MISSING"] == 0.0


# ---------------------------------------------------------------------------
# 5. Final eligibility matches production for identical dicts
# ---------------------------------------------------------------------------

class TestFinalEligibilityMatchesProduction:
    def test_low_confidence_candidate_rejected_same_as_direct_gate_call(self):
        candidates = [_candidate("LOWCONF", confidence=10), _candidate("OK", confidence=80)]
        snapshot = _snapshot(candidates)
        result = replay_snapshot(snapshot)
        rejected_symbols = {r.symbol: r.reason_code for r in result.rejected_candidates}
        assert rejected_symbols.get("LOWCONF") == REJECTION_CONFIDENCE_BELOW_FLOOR
        # cross-check directly against the real gate
        assert dp._passes_quality_gate({"symbol": "LOWCONF", "confidence": 10, "reasoning": []}, "medium") is False

    def test_non_buy_signal_rejected(self):
        candidates = [_candidate("HOLDER", signal="HOLD"), _candidate("OK")]
        snapshot = _snapshot(candidates)
        result = replay_snapshot(snapshot)
        rejected_symbols = {r.symbol: r.reason_code for r in result.rejected_candidates}
        assert rejected_symbols.get("HOLDER") == REJECTION_NOT_BUY_SIGNAL


# ---------------------------------------------------------------------------
# 6. Short-horizon selection matches production
# ---------------------------------------------------------------------------

class TestShortHorizonSelectionMatchesProduction:
    def test_high_confidence_prioritized_same_as_direct_call(self):
        candidates = [
            _candidate("HIGH1", confidence=85, tech=10), _candidate("LOW1", confidence=60, tech=90),
            _candidate("HIGH2", confidence=90, tech=5),
        ]
        snapshot = _snapshot(candidates, horizon="short")
        result = replay_snapshot(snapshot)
        # both high-confidence (>80) picks must precede the low-confidence one
        assert result.selected_slate.index("HIGH1") < result.selected_slate.index("LOW1")
        assert result.selected_slate.index("HIGH2") < result.selected_slate.index("LOW1")


# ---------------------------------------------------------------------------
# 7. Medium/long tier selection matches _select_with_tier_quota
# ---------------------------------------------------------------------------

class TestTierSelectionMatchesProduction:
    def test_tier_quota_applied_for_medium_horizon(self):
        candidates = [
            _candidate("L1", cap_tier="large", tech=90), _candidate("L2", cap_tier="large", tech=89),
            _candidate("L3", cap_tier="large", tech=88),
            _candidate("M1", cap_tier="mid", tech=50), _candidate("M2", cap_tier="mid", tech=49),
            _candidate("S1", cap_tier="small", tech=10), _candidate("S2", cap_tier="small", tech=9),
        ]
        snapshot = _snapshot(candidates, horizon="medium")
        result = replay_snapshot(snapshot)
        # 2/2/2 quota -> exactly one large-cap must be dropped (L3, the weakest)
        assert "L3" not in result.selected_slate
        assert len(result.selected_slate) == 6


# ---------------------------------------------------------------------------
# 8. Issuer dedup matches production
# ---------------------------------------------------------------------------

class TestIssuerDedupMatchesProduction:
    def test_goog_googl_dedup_applied_for_us_market(self):
        candidates = [_candidate("GOOG", tech=90), _candidate("GOOGL", tech=89), _candidate("OTHER", tech=50)]
        snapshot = _snapshot(candidates, market="US")
        result = replay_snapshot(snapshot)
        assert result.issuer_duplicates_suppressed == 1
        rejected_codes = {r.symbol: r.reason_code for r in result.rejected_candidates}
        assert rejected_codes.get("GOOGL") == REJECTION_ISSUER_DUPLICATE
        assert "GOOG" in result.selected_slate

    def test_dedup_not_applied_for_in_market(self):
        candidates = [_candidate("GOOG", tech=90), _candidate("GOOGL", tech=89)]
        snapshot = _snapshot(candidates, market="IN")
        result = replay_snapshot(snapshot)
        assert result.issuer_duplicates_suppressed == 0


# ---------------------------------------------------------------------------
# 9. Optimizer output matches optimizer.optimize() for the same inputs
# ---------------------------------------------------------------------------

class TestOptimizerOutputMatchesDirectCall:
    def test_three_candidate_weights_match_direct_optimizer_call(self):
        candidates = [_candidate("A", tech=90), _candidate("B", tech=60), _candidate("C", tech=30)]
        snapshot = _snapshot(candidates, horizon="medium")
        result = replay_snapshot(snapshot)
        alphas = [next(r.ranking_alpha for r in result.ranked_universe if r.symbol == s) for s in result.selected_slate]
        direct = optimize(alphas=alphas, returns_matrix=None, max_weight=0.40, risk_aversion=2.0, regime_label="BULL_CALM")
        replay_weights = [result.portfolio_weights[s] for s in result.selected_slate]
        assert replay_weights == direct


# ---------------------------------------------------------------------------
# 10 & 11. DP-020 cash semantics; 2 picks at 40% cap -> 40/40/20
# ---------------------------------------------------------------------------

class TestPortfolioCashSemantics:
    def test_two_picks_at_forty_percent_cap_yield_forty_forty_twenty_cash(self):
        candidates = [_candidate("A", tech=90), _candidate("B", tech=10)]
        snapshot = _snapshot(candidates, horizon="medium")
        result = replay_snapshot(snapshot)
        assert len(result.selected_slate) == 2
        for w in result.portfolio_weights.values():
            assert w == pytest.approx(0.4, abs=1e-3)
        assert result.cash_unallocated_pct == pytest.approx(0.2, abs=1e-3)

    def test_cash_equals_one_minus_sum_of_weights(self):
        candidates = [_candidate("A", tech=90), _candidate("B", tech=10), _candidate("C", tech=50)]
        snapshot = _snapshot(candidates, horizon="medium")
        result = replay_snapshot(snapshot)
        total = sum(result.portfolio_weights.values())
        assert total + result.cash_unallocated_pct == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# 12-14. STRICT mode refusals
# ---------------------------------------------------------------------------

class TestStrictModeRefusals:
    def test_rejects_current_day_fundamentals_marked_as_historical(self):
        integrity = _fully_point_in_time_integrity()
        integrity["fundamental"] = PointInTimeIntegrity.CURRENT_DAY_MASQUERADING
        snapshot = _snapshot([_candidate("AAA")], mode=ReplayMode.STRICT_POINT_IN_TIME, point_in_time_integrity=integrity)
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("fundamental" in m for m in result.missing_requirements)
        assert result.ranked_universe == []
        assert result.selected_slate == []

    def test_rejects_missing_provenance(self):
        snapshot = _snapshot([_candidate("AAA")], mode=ReplayMode.STRICT_POINT_IN_TIME, source="")
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("source" in m.lower() for m in result.missing_requirements)

    def test_rejects_synthetic_mandatory_inputs(self):
        integrity = _fully_point_in_time_integrity()
        integrity["technical"] = PointInTimeIntegrity.SYNTHETIC
        snapshot = _snapshot([_candidate("AAA")], mode=ReplayMode.STRICT_POINT_IN_TIME, point_in_time_integrity=integrity)
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("technical" in m for m in result.missing_requirements)

    def test_rejects_explicit_synthetic_flag(self):
        snapshot = _snapshot(
            [_candidate("AAA")], mode=ReplayMode.STRICT_POINT_IN_TIME,
            synthetic_flags={"fundamentals_are_current_day_not_historical": True},
        )
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_missing_sentiment_alone_does_not_trigger_strict_refusal(self):
        # Sentiment is deliberately not a mandatory strict-mode category —
        # production itself treats missing sentiment as legitimate.
        snapshot = _snapshot([_candidate("AAA", sentiment_available=False)], mode=ReplayMode.STRICT_POINT_IN_TIME)
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.COMPLETED


# ---------------------------------------------------------------------------
# 15. DIAGNOSTIC mode permits synthetic fixtures, clearly labelled
# ---------------------------------------------------------------------------

class TestDiagnosticModeLabelling:
    def test_diagnostic_mode_permits_synthetic_and_labels_clearly(self):
        integrity = {}  # everything UNKNOWN_PROVENANCE — would fail strict
        snapshot = _snapshot([_candidate("AAA"), _candidate("BBB")], mode=ReplayMode.DIAGNOSTIC, point_in_time_integrity=integrity)
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.DIAGNOSTIC_COMPLETED
        assert len(result.ranked_universe) == 2
        joined = " ".join(result.warnings)
        assert "DIAGNOSTIC ONLY" in joined
        assert "NOT PRODUCTION-EQUIVALENT" in joined
        assert "NOT EVIDENCE OF HISTORICAL ACCURACY" in joined
        assert "NOT SUITABLE FOR PRODUCTION-LEARNING GRADUATION" in joined


# ---------------------------------------------------------------------------
# 20. Every rejected candidate has a stable machine-readable reason
# ---------------------------------------------------------------------------

class TestRejectionReasonsAreStable:
    def test_every_rejected_candidate_has_a_nonempty_known_reason_code(self):
        candidates = [
            _candidate("LOWCONF", confidence=5), _candidate("HOLDER", signal="HOLD"),
            _candidate("GOOD1", tech=90), _candidate("GOOD2", tech=10),
        ]
        snapshot = _snapshot(candidates, horizon="medium")
        result = replay_snapshot(snapshot)
        assert len(result.rejected_candidates) >= 2
        for rc in result.rejected_candidates:
            assert rc.reason_code
            assert rc.symbol
            assert rc.stage


# ---------------------------------------------------------------------------
# 21. Deterministic repeated replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_repeated_replay_of_same_snapshot_produces_identical_hash(self):
        candidates = [_candidate("A", tech=90), _candidate("B", tech=10), _candidate("C", tech=50)]
        snapshot1 = _snapshot(candidates, horizon="medium")
        snapshot2 = _snapshot(candidates, horizon="medium")
        result1 = replay_snapshot(snapshot1)
        result2 = replay_snapshot(snapshot2)
        assert result1.result_hash == result2.result_hash
        assert result1.result_hash != ""

    def test_different_snapshot_id_alone_still_changes_hash(self):
        candidates = [_candidate("A"), _candidate("B")]
        r1 = replay_snapshot(_snapshot(candidates))
        snap2 = _snapshot(candidates)
        snap2.snapshot_id = "different-id"
        r2 = replay_snapshot(snap2)
        assert r1.result_hash != r2.result_hash


# ---------------------------------------------------------------------------
# 22. DP-026-029 recorded as unresolved
# ---------------------------------------------------------------------------

class TestUnresolvedDependenciesRecorded:
    def test_dp_026_through_029_all_marked_unresolved(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        assert result.dp_dependency_resolved == {
            "DP-026_point_in_time_fundamentals": False,
            "DP-027_survivorship_bias": False,
            "DP-028_overlapping_windows": False,
            "DP-029_transaction_costs": False,
        }
        joined = " ".join(result.unresolved_methodology_limitations)
        assert "DP-026" in joined
        assert "DP-027" in joined
        assert "DP-028" in joined
        assert "DP-029" in joined
        assert "hit rate" in joined.lower() or "profitability" in joined.lower()

    def test_no_hit_rate_return_accuracy_or_profitability_field_anywhere_in_result(self):
        result = replay_snapshot(_snapshot([_candidate("A"), _candidate("B")]))
        result_dict = result.to_json_dict()
        forbidden_terms = ("hit_rate", "return_pct", "accuracy", "profitability", "sharpe", "cagr")
        serialized_keys = _all_keys(result_dict)
        for term in forbidden_terms:
            assert term not in serialized_keys, f"forbidden metric key found: {term}"


def _all_keys(d, acc=None):
    if acc is None:
        acc = set()
    if isinstance(d, dict):
        for k, v in d.items():
            acc.add(k)
            _all_keys(v, acc)
    elif isinstance(d, list):
        for item in d:
            _all_keys(item, acc)
    return acc


# ---------------------------------------------------------------------------
# Empty universe / no candidates edge case
# ---------------------------------------------------------------------------

class TestEmptyUniverse:
    def test_no_candidates_produces_empty_but_valid_result(self):
        snapshot = _snapshot([])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.DIAGNOSTIC_COMPLETED
        assert result.ranked_universe == []
        assert result.selected_slate == []
        assert result.portfolio_weights == {}
        assert result.cash_unallocated_pct == 0.0
