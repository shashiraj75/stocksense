"""
DP-019 — deterministic, offline, shadow-only A/B comparator for
services/validation/regime_multiplier_ab.py.

No owner decision was required or made to build this comparator — it
evaluates the currently CONTAINED production weighting path (academic-prior
IC -> optional REGIME_WEIGHT_MULTIPLIERS -> normalised factor weights via
the real ic_engine._prior_weights()) against a no-multiplier baseline, for
one explicitly supplied MarketSnapshot. It never changes or approves
REGIME_WEIGHT_MULTIPLIERS, production ranking, production factor weights,
production-learning containment, or any DP-017/DP-018 regime behaviour.

Deterministic fixtures only. No network, no production database access, no
Daily Picks generation, no model retraining. This file does not rely
solely on source-code text assertions — every test drives the real
compare_regime_multiplier() entry point (which itself drives the real
replay_snapshot()) and asserts on structured output.
"""

import math
from unittest.mock import patch

import pytest

import services.alpha_engine.regime_cluster as rc
from services.alpha_engine.ic_engine import _prior_weights as prod_prior_weights
from services.validation.contracts import (
    MarketSnapshot, CandidateSnapshot, ReplayMode, ReplayStatus, PointInTimeIntegrity,
)
from services.validation.regime_multiplier_ab import (
    compare_regime_multiplier,
    ABStatus,
    WEIGHT_SOURCE_CONTAINED_ACADEMIC_PRIOR,
    DP019_IC_WEIGHTS_DIAGNOSTIC_ONLY_FLAG,
    DP019_LIMITATIONS,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _production_multiplier_map(label="BULL_CALM"):
    # Falls back to BULL_CALM's map for an unrecognised label — used by
    # invalid-regime-label refusal tests, where the multiplier map's
    # content is irrelevant (the label mismatch itself is what refuses).
    return dict(rc.REGIME_WEIGHT_MULTIPLIERS.get(label, rc.REGIME_WEIGHT_MULTIPLIERS["BULL_CALM"]))


def _candidate(symbol, confidence=70, tech=60.0, fund=55.0, sentiment=50.0, quality=52.0,
               sentiment_available=True, quality_available=True, signal="BUY",
               reasoning=None, cap_tier="large"):
    return CandidateSnapshot(
        symbol=symbol, signal=signal, confidence=confidence,
        technical_score=tech, fundamental_score=fund,
        sentiment_score=sentiment if sentiment_available else None,
        sentiment_available=sentiment_available,
        quality_score=quality if quality_available else 50,
        quality_available=quality_available,
        quality_raw_score=quality if quality_available else None,
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


def _valid_snapshot(candidates, horizon="medium", market="US", regime_label="BULL_CALM",
                     regime_id=0, mode=ReplayMode.DIAGNOSTIC, returns_by_symbol=None,
                     synthetic_flags=None, snapshot_id="dp019-snap",
                     as_of="2026-01-01T00:00:00Z"):
    mult = _production_multiplier_map(regime_label)
    on_weights = prod_prior_weights(horizon, market, mult)
    return MarketSnapshot(
        snapshot_id=snapshot_id, as_of=as_of, market=market, horizon=horizon,
        source="manual_fixture", replay_mode=mode, candidates=candidates,
        regime_id=regime_id, regime_label=regime_label,
        regime_weight_multipliers=mult,
        ic_weights=on_weights,
        point_in_time_integrity=_fully_point_in_time_integrity(),
        synthetic_flags=synthetic_flags or {},
        returns_by_symbol=returns_by_symbol,
    )


# ---------------------------------------------------------------------------
# 1-3 — Branch A/B call the real _prior_weights() correctly
# ---------------------------------------------------------------------------

class TestBranchesCallRealPriorWeights:
    def test_branch_a_matches_direct_prior_weights_call_with_production_multipliers(self):
        candidates = [_candidate("AAA"), _candidate("BBB", tech=40, fund=70)]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)
        expected_on = prod_prior_weights("medium", "US", rc.REGIME_WEIGHT_MULTIPLIERS["BULL_CALM"])
        assert result.multiplier_on_weights == expected_on

    def test_branch_b_matches_direct_prior_weights_call_with_none(self):
        candidates = [_candidate("AAA"), _candidate("BBB", tech=40, fund=70)]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)
        expected_off = prod_prior_weights("medium", "US", None)
        assert result.multiplier_off_weights == expected_off

    def test_production_multiplier_map_is_taken_from_regime_weight_multipliers(self):
        candidates = [_candidate("AAA"), _candidate("BBB")]
        for label in ("BULL_CALM", "BULL_VOLATILE", "BEAR_CALM", "BEAR_PANIC"):
            regime_id = [k for k, v in rc.REGIME_LABELS.items() if v == label][0]
            snapshot = _valid_snapshot(candidates, regime_label=label, regime_id=regime_id)
            result = compare_regime_multiplier(snapshot)
            assert result.multiplier_map == rc.REGIME_WEIGHT_MULTIPLIERS[label]

    def test_weight_source_is_contained_academic_prior(self):
        candidates = [_candidate("AAA"), _candidate("BBB")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)
        assert result.weight_source == WEIGHT_SOURCE_CONTAINED_ACADEMIC_PRIOR
        assert result.weight_source == "contained_academic_prior"


# ---------------------------------------------------------------------------
# 4-8 — input validation refusals
# ---------------------------------------------------------------------------

class TestInputValidationRefusals:
    def test_invalid_regime_label_refuses(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, regime_label="NOT_A_REAL_REGIME", regime_id=0)
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value
        assert any("regime_label" in e for e in result.validation_errors)
        assert result.multiplier_on_replay is None
        assert result.multiplier_off_replay is None

    def test_mismatched_snapshot_multiplier_map_refuses(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        snapshot.regime_weight_multipliers = {"tech": 999.0, "fund": 0.9, "sentiment": 1.1, "quality": 1.0}
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value
        assert any("does not match current production" in e for e in result.validation_errors)

    def test_missing_multiplier_key_refuses(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        del snapshot.regime_weight_multipliers["quality"]
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value
        assert any("missing key" in e for e in result.validation_errors)

    def test_extra_multiplier_key_refuses(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        snapshot.regime_weight_multipliers["extra_factor"] = 1.0
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value
        assert any("extra key" in e for e in result.validation_errors)

    @pytest.mark.parametrize("bad_value", [-0.5, float("nan"), float("inf"), float("-inf")])
    def test_negative_nan_infinite_multiplier_refuses(self, bad_value):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        snapshot.regime_weight_multipliers["tech"] = bad_value
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value
        assert any("not a finite non-negative number" in e for e in result.validation_errors)


# ---------------------------------------------------------------------------
# 9 — mismatched ic_weights refuse in strict/current-production mode
# ---------------------------------------------------------------------------

class TestMismatchedIcWeightsRefuse:
    def test_mismatched_ic_weights_refuse_by_default(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        snapshot.ic_weights = {"tech": 0.99, "fund": 0.01, "sentiment": 0.0, "quality": 0.0}
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value
        assert any("ic_weights" in e for e in result.validation_errors)
        assert any("different weight source" in e for e in result.validation_errors)

    def test_diagnostic_bypass_flag_allows_mismatch_only_in_diagnostic_mode(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, mode=ReplayMode.DIAGNOSTIC)
        snapshot.ic_weights = {"tech": 0.99, "fund": 0.01, "sentiment": 0.0, "quality": 0.0}
        snapshot.synthetic_flags[DP019_IC_WEIGHTS_DIAGNOSTIC_ONLY_FLAG] = True
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.COMPLETED.value
        # Result remains clearly non-production-equivalent regardless.
        assert result.production_equivalent is False

    def test_diagnostic_bypass_flag_does_not_apply_in_strict_mode(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, mode=ReplayMode.STRICT_POINT_IN_TIME)
        snapshot.ic_weights = {"tech": 0.99, "fund": 0.01, "sentiment": 0.0, "quality": 0.0}
        snapshot.synthetic_flags[DP019_IC_WEIGHTS_DIAGNOSTIC_ONLY_FLAG] = True
        result = compare_regime_multiplier(snapshot)
        assert result.status == ABStatus.REFUSED_INVALID_INPUT.value


# ---------------------------------------------------------------------------
# 10-12 — branch isolation: identical candidates/metadata, only ic_weights
# differs, and the input snapshot is never mutated
# ---------------------------------------------------------------------------

class TestBranchIsolation:
    def test_both_branches_receive_identical_candidates_and_metadata(self):
        candidates = [_candidate("AAA"), _candidate("BBB", tech=30, fund=80)]
        snapshot = _valid_snapshot(candidates, returns_by_symbol={"AAA": [0.01] * 30, "BBB": [0.02] * 30})
        result = compare_regime_multiplier(snapshot)

        on_r = result.multiplier_on_replay
        off_r = result.multiplier_off_replay
        assert on_r.market == off_r.market == "US"
        assert on_r.horizon == off_r.horizon == "medium"
        on_symbols = {c.symbol for c in on_r.ranked_universe}
        off_symbols = {c.symbol for c in off_r.ranked_universe}
        assert on_symbols == off_symbols == {"AAA", "BBB"}

    def test_only_ic_weights_differs_between_branch_snapshots(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)

        captured_snapshots = []
        from services.validation import regime_multiplier_ab as mod
        real_replay = mod.replay_snapshot

        def spy_replay(s):
            captured_snapshots.append(s)
            return real_replay(s)

        with patch("services.validation.regime_multiplier_ab.replay_snapshot", side_effect=spy_replay):
            compare_regime_multiplier(snapshot)

        assert len(captured_snapshots) == 2
        on_s, off_s = captured_snapshots
        assert on_s.ic_weights != off_s.ic_weights
        # everything else identical
        assert on_s.market == off_s.market
        assert on_s.horizon == off_s.horizon
        assert on_s.regime_id == off_s.regime_id
        assert on_s.regime_label == off_s.regime_label
        assert on_s.regime_weight_multipliers == off_s.regime_weight_multipliers
        assert [c.symbol for c in on_s.candidates] == [c.symbol for c in off_s.candidates]
        assert on_s.returns_by_symbol == off_s.returns_by_symbol
        assert on_s.point_in_time_integrity == off_s.point_in_time_integrity

    def test_input_snapshot_is_never_mutated(self):
        candidates = [_candidate("AAA"), _candidate("BBB")]
        snapshot = _valid_snapshot(candidates)
        original_ic_weights = dict(snapshot.ic_weights)
        original_candidates_order = [c.symbol for c in snapshot.candidates]

        compare_regime_multiplier(snapshot)

        assert snapshot.ic_weights == original_ic_weights
        assert [c.symbol for c in snapshot.candidates] == original_candidates_order


# ---------------------------------------------------------------------------
# 13-14 — both branches call the real replay_snapshot(); production
# learning forced off through the replay path
# ---------------------------------------------------------------------------

class TestBothBranchesUseRealReplay:
    def test_replay_snapshot_called_exactly_twice(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        from services.validation.pipeline_replay import replay_snapshot as real_replay_snapshot
        with patch(
            "services.validation.regime_multiplier_ab.replay_snapshot",
            wraps=real_replay_snapshot,
        ) as spy:
            compare_regime_multiplier(snapshot)
        assert spy.call_count == 2

    def test_production_learning_never_activated(self, monkeypatch):
        monkeypatch.setenv("LEARNING_ALPHA_PRODUCTION_ENABLED", "1")
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        with patch("services.alpha_engine.meta_model.predict", return_value=999.0):
            result = compare_regime_multiplier(snapshot)
        for r in result.multiplier_on_replay.ranked_universe + result.multiplier_off_replay.ranked_universe:
            assert r.meta_alpha_used_for_ranking is False


# ---------------------------------------------------------------------------
# 15 — neutral all-ones multiplier is a mathematical no-op for _prior_weights
# ---------------------------------------------------------------------------

class TestNeutralMultiplierIsNoOp:
    def test_all_ones_multiplier_equals_no_multiplier(self):
        neutral = {"tech": 1.0, "fund": 1.0, "sentiment": 1.0, "quality": 1.0}
        for market in ("IN", "US"):
            for horizon in ("short", "medium", "long"):
                with_neutral = prod_prior_weights(horizon, market, neutral)
                without = prod_prior_weights(horizon, market, None)
                assert with_neutral == without


# ---------------------------------------------------------------------------
# 16-18 — engineered fixture: known rank change, correct rank-delta
# convention, correct combined-alpha deltas
# ---------------------------------------------------------------------------

class TestEngineeredRankChange:
    def _fixture(self):
        # BULL_CALM boosts tech (x1.3), reduces fund (x0.9) — a tech-heavy
        # candidate should be relatively favoured with multipliers ON; a
        # fund-heavy candidate relatively favoured with multipliers OFF.
        return [
            _candidate("TECH_HEAVY", tech=95.0, fund=20.0, sentiment=50.0, quality=50.0),
            _candidate("FUND_HEAVY", tech=20.0, fund=95.0, sentiment=50.0, quality=50.0),
        ]

    def test_engineered_fixture_produces_a_known_rank_change(self):
        candidates = self._fixture()
        snapshot = _valid_snapshot(candidates, regime_label="BULL_CALM", regime_id=0)
        result = compare_regime_multiplier(snapshot)

        by_symbol = {d["symbol"]: d for d in result.rank_deltas}
        tech_heavy_delta = by_symbol["TECH_HEAVY"]["rank_delta"]
        assert tech_heavy_delta is not None
        assert tech_heavy_delta != 0, "engineered fixture must produce a real rank change"

    def test_rank_delta_convention_matches_independent_computation(self):
        import services.daily_picks as dp
        candidates = self._fixture()
        snapshot = _valid_snapshot(candidates, regime_label="BULL_CALM", regime_id=0)
        result = compare_regime_multiplier(snapshot)

        rows = [
            {"symbol": c.symbol, "horizon": "medium", "signal": c.signal, "confidence": c.confidence,
             "tech_score": c.technical_score, "fund_score": c.fundamental_score,
             "sentiment_score": c.sentiment_score, "quality_score": c.quality_score,
             "reasoning": c.reasoning, "cap_tier": c.cap_tier}
            for c in candidates
        ]
        on_ranked = sorted(
            dp._zscore_and_rank(rows, result.multiplier_on_weights, {"label": "BULL_CALM"}, 0, market="US",
                                 production_learning_enabled=False),
            key=lambda x: x.get("ranking_alpha", 0), reverse=True,
        )
        off_ranked = sorted(
            dp._zscore_and_rank(rows, result.multiplier_off_weights, {"label": "BULL_CALM"}, 0, market="US",
                                 production_learning_enabled=False),
            key=lambda x: x.get("ranking_alpha", 0), reverse=True,
        )
        expected_on_rank = {r["symbol"]: i + 1 for i, r in enumerate(on_ranked)}
        expected_off_rank = {r["symbol"]: i + 1 for i, r in enumerate(off_ranked)}

        by_symbol = {d["symbol"]: d for d in result.rank_deltas}
        for symbol in expected_on_rank:
            expected_delta = expected_off_rank[symbol] - expected_on_rank[symbol]
            assert by_symbol[symbol]["rank_with_multiplier"] == expected_on_rank[symbol]
            assert by_symbol[symbol]["rank_without_multiplier"] == expected_off_rank[symbol]
            assert by_symbol[symbol]["rank_delta"] == expected_delta

    def test_combined_alpha_deltas_are_correct(self):
        candidates = self._fixture()
        snapshot = _valid_snapshot(candidates, regime_label="BULL_CALM", regime_id=0)
        result = compare_regime_multiplier(snapshot)

        on_alphas = {r.symbol: r.combined_alpha for r in result.multiplier_on_replay.ranked_universe}
        off_alphas = {r.symbol: r.combined_alpha for r in result.multiplier_off_replay.ranked_universe}
        by_symbol = {d["symbol"]: d for d in result.rank_deltas}
        for symbol in on_alphas:
            expected = on_alphas[symbol] - off_alphas[symbol]
            assert by_symbol[symbol]["combined_alpha_delta"] == pytest.approx(expected)
            assert by_symbol[symbol]["combined_alpha_with_multiplier"] == pytest.approx(on_alphas[symbol])
            assert by_symbol[symbol]["combined_alpha_without_multiplier"] == pytest.approx(off_alphas[symbol])


# ---------------------------------------------------------------------------
# 19-21 — slate comparison correctness
# ---------------------------------------------------------------------------

class TestSlateComparison:
    def test_selected_slate_overlap_correct(self):
        candidates = [_candidate(f"S{i}", tech=50 + i, fund=50 - i) for i in range(6)]
        snapshot = _valid_snapshot(candidates, horizon="short")
        result = compare_regime_multiplier(snapshot)

        on_selected = set(result.multiplier_on_replay.selected_slate)
        off_selected = set(result.multiplier_off_replay.selected_slate)
        assert result.selected_slate_overlap == len(on_selected & off_selected)
        union = on_selected | off_selected
        expected_ratio = (len(on_selected & off_selected) / len(union)) if union else 1.0
        assert result.selected_slate_overlap_ratio == pytest.approx(expected_ratio)

    def test_selected_only_with_and_without_sets_correct(self):
        candidates = [_candidate(f"S{i}", tech=50 + i * 5, fund=50 - i * 5) for i in range(6)]
        snapshot = _valid_snapshot(candidates, horizon="short")
        result = compare_regime_multiplier(snapshot)

        on_selected = set(result.multiplier_on_replay.selected_slate)
        off_selected = set(result.multiplier_off_replay.selected_slate)
        assert set(result.selected_only_with_multiplier) == on_selected - off_selected
        assert set(result.selected_only_without_multiplier) == off_selected - on_selected
        assert set(result.selected_in_both) == on_selected & off_selected

    def test_common_selection_order_change_detection_correct(self):
        candidates = [_candidate(f"S{i}", tech=50 + i * 5, fund=50 - i * 5) for i in range(6)]
        snapshot = _valid_snapshot(candidates, horizon="short")
        result = compare_regime_multiplier(snapshot)

        common = set(result.selected_in_both)
        on_order = [s for s in result.multiplier_on_replay.selected_slate if s in common]
        off_order = [s for s in result.multiplier_off_replay.selected_slate if s in common]
        assert result.selected_order_changed == (on_order != off_order)


# ---------------------------------------------------------------------------
# 22-23 — portfolio weight deltas and cash delta correctness
# ---------------------------------------------------------------------------

class TestPortfolioComparison:
    def test_portfolio_weight_deltas_correct(self):
        candidates = [_candidate("AAA", tech=90, fund=20), _candidate("BBB", tech=20, fund=90)]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)

        on_w = result.multiplier_on_replay.portfolio_weights
        off_w = result.multiplier_off_replay.portfolio_weights
        all_symbols = set(on_w) | set(off_w)
        for symbol in all_symbols:
            expected = round(on_w.get(symbol, 0.0) - off_w.get(symbol, 0.0), 6)
            assert result.portfolio_weight_deltas[symbol] == pytest.approx(expected)

    def test_cash_delta_correct(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)

        on_cash = result.multiplier_on_replay.cash_unallocated_pct
        off_cash = result.multiplier_off_replay.cash_unallocated_pct
        assert on_cash is not None and off_cash is not None
        assert result.cash_unallocated_delta == pytest.approx(round(on_cash - off_cash, 6))


# ---------------------------------------------------------------------------
# 24-26 — zero/one/two-pick branches preserve DP-020/DP-021 contracts
# ---------------------------------------------------------------------------

class TestZeroOneTwoPickPortfolioContracts:
    def test_zero_pick_branches_compare_safely(self):
        candidates = [_candidate("AAA", signal="HOLD"), _candidate("BBB", signal="HOLD")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)

        assert result.status == ABStatus.COMPLETED.value
        assert result.multiplier_on_replay.selected_slate == []
        assert result.multiplier_off_replay.selected_slate == []
        assert result.portfolio_weight_deltas == {}
        assert result.cash_unallocated_delta == pytest.approx(0.0)
        assert result.selected_slate_overlap == 0
        assert result.selected_slate_overlap_ratio == pytest.approx(1.0)

    def test_one_pick_branches_preserve_forty_sixty_contract(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)

        for replay in (result.multiplier_on_replay, result.multiplier_off_replay):
            assert len(replay.selected_slate) == 1
            symbol = replay.selected_slate[0]
            assert replay.portfolio_weights[symbol] == pytest.approx(0.40)
            assert replay.cash_unallocated_pct == pytest.approx(0.60)

    def test_two_pick_branches_preserve_forty_forty_twenty_cash_contract(self):
        candidates = [_candidate("AAA", tech=60, fund=60), _candidate("BBB", tech=59, fund=59)]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)

        for replay in (result.multiplier_on_replay, result.multiplier_off_replay):
            assert len(replay.selected_slate) == 2
            for w in replay.portfolio_weights.values():
                assert w == pytest.approx(0.40, abs=1e-3)
            assert replay.cash_unallocated_pct == pytest.approx(0.20, abs=1e-3)


# ---------------------------------------------------------------------------
# 27-28 — strict refusal propagation; diagnostic disclaimers propagate
# ---------------------------------------------------------------------------

class TestRefusalAndDiagnosticPropagation:
    def test_strict_underlying_refusal_propagates_without_manufactured_comparisons(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, mode=ReplayMode.STRICT_POINT_IN_TIME)
        snapshot.point_in_time_integrity["technical"] = PointInTimeIntegrity.SYNTHETIC
        result = compare_regime_multiplier(snapshot)

        assert result.status == ABStatus.REFUSED_UNDERLYING_REPLAY.value
        assert result.multiplier_on_replay.status == ReplayStatus.REFUSED_INCOMPLETE
        assert result.multiplier_off_replay.status == ReplayStatus.REFUSED_INCOMPLETE
        assert result.rank_deltas == []
        assert result.selected_only_with_multiplier == []
        assert result.selected_only_without_multiplier == []
        assert result.portfolio_weight_deltas == {}
        assert result.selected_slate_overlap == 0

    def test_diagnostic_disclaimers_propagate(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, mode=ReplayMode.DIAGNOSTIC)
        result = compare_regime_multiplier(snapshot)

        assert any("DIAGNOSTIC ONLY." in w for w in result.warnings)
        assert any("NOT PRODUCTION-EQUIVALENT." in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 29 — fixed false flags can never become true
# ---------------------------------------------------------------------------

class TestFixedFalseFlags:
    def test_flags_false_on_completed_result(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)
        assert result.production_equivalent is False
        assert result.performance_validated is False
        assert result.multiplier_policy_approved is False

    def test_flags_false_on_refused_invalid_input(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, regime_label="BOGUS", regime_id=0)
        result = compare_regime_multiplier(snapshot)
        assert result.production_equivalent is False
        assert result.performance_validated is False
        assert result.multiplier_policy_approved is False

    def test_flags_false_on_refused_underlying_replay(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, mode=ReplayMode.STRICT_POINT_IN_TIME)
        snapshot.point_in_time_integrity["fundamental"] = PointInTimeIntegrity.MISSING
        result = compare_regime_multiplier(snapshot)
        assert result.production_equivalent is False
        assert result.performance_validated is False
        assert result.multiplier_policy_approved is False

    def test_limitations_present_on_completed_result(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)
        assert result.limitations == list(DP019_LIMITATIONS)


# ---------------------------------------------------------------------------
# 30 — deterministic hashing
# ---------------------------------------------------------------------------

class TestDeterministicHashing:
    def test_repeated_execution_produces_same_hash(self):
        candidates = [_candidate("AAA"), _candidate("BBB", tech=30, fund=80)]
        snapshot = _valid_snapshot(candidates)
        result_1 = compare_regime_multiplier(snapshot)
        result_2 = compare_regime_multiplier(snapshot)
        assert result_1.result_hash == result_2.result_hash
        assert result_1.result_hash != ""

    def test_hash_works_for_refused_invalid_input(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, regime_label="BOGUS", regime_id=0)
        result = compare_regime_multiplier(snapshot)
        assert result.result_hash != ""

    def test_hash_works_for_refused_underlying_replay(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates, mode=ReplayMode.STRICT_POINT_IN_TIME)
        snapshot.point_in_time_integrity["quality"] = PointInTimeIntegrity.MISSING
        result = compare_regime_multiplier(snapshot)
        assert result.result_hash != ""


# ---------------------------------------------------------------------------
# 31-35 — side-effect prohibitions
# ---------------------------------------------------------------------------

class TestSideEffectProhibitions:
    def test_no_network_function_called(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)

        def _boom(*a, **kw):
            raise AssertionError("network function must not be called")

        with patch("yfinance.Ticker", side_effect=_boom), \
             patch("services.daily_picks.yf.download", side_effect=_boom):
            compare_regime_multiplier(snapshot)

    def test_no_persistence_function_called(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)

        def _boom(*a, **kw):
            raise AssertionError("persistence function must not be called")

        with patch("services.alpha_engine.store.log_prediction", side_effect=_boom), \
             patch("services.daily_picks._write_score_snapshots", side_effect=_boom):
            compare_regime_multiplier(snapshot)

    def test_no_daily_picks_generation_triggered(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)

        def _boom(*a, **kw):
            raise AssertionError("Daily Picks generation must not be triggered")

        with patch("services.daily_picks.generate_picks", side_effect=_boom), \
             patch("services.daily_picks._generate_picks_inner", side_effect=_boom):
            compare_regime_multiplier(snapshot)

    def test_no_model_retraining_occurs(self):
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)

        def _boom(*a, **kw):
            raise AssertionError("model retraining must not occur")

        with patch("services.alpha_engine.regime_cluster.retrain_on_history", side_effect=_boom):
            compare_regime_multiplier(snapshot)

    def test_no_production_learning_environment_setting_changed(self, monkeypatch):
        monkeypatch.delenv("LEARNING_ALPHA_PRODUCTION_ENABLED", raising=False)
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        compare_regime_multiplier(snapshot)
        import os
        assert os.environ.get("LEARNING_ALPHA_PRODUCTION_ENABLED") is None


# ---------------------------------------------------------------------------
# 36-39 — pre-existing suites unaffected (imported here as a light smoke
# check; the full suites are run separately by the related-suite command)
# ---------------------------------------------------------------------------

class TestNoRegressionInAdjacentContracts:
    def test_replay_result_production_provenance_comment_correction_did_not_change_behavior(self):
        # The DP-019 task required correcting a stale comment in
        # contracts.py describing production_provenance — this asserts the
        # actual (already-correct since DP-021) runtime behavior it now
        # accurately describes: optimizer invoked for any non-empty slate.
        candidates = [_candidate("AAA")]
        snapshot = _valid_snapshot(candidates)
        result = compare_regime_multiplier(snapshot)
        on_replay = result.multiplier_on_replay
        assert on_replay.production_provenance["invoked"].get("optimizer") == "services.alpha_engine.optimizer.optimize"
        assert "optimizer" not in on_replay.production_provenance["not_invoked"]
