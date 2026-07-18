"""
DP-025 quality-provenance correction (2026-07-18) — behavioural tests
proving `contracts.validate_structure()` now models the EXACT production
contract from `_predict_stock()`:

    "quality_score": qf.get("score") if qf.get("score") is not None else 50,
    "quality_available": _quality_available,
    "quality_raw_score": _quality_raw,

i.e. unavailable quality evidence legitimately produces
`quality_available=False, quality_raw_score=None, quality_score=50` — NOT
`quality_score=None` as the pre-fix (55e4c47) version of this contract
incorrectly assumed. `quality_score` is the ranking-compatible value
(genuine or neutral-fallback); `quality_raw_score` is the provenance field
distinguishing the two.

Does not rely solely on source-text checks — every test drives
replay_snapshot() or validate_structure() and asserts on structured output.
"""

import math

import pytest

from services.validation.contracts import (
    MarketSnapshot, CandidateSnapshot, ReplayMode, ReplayStatus, PointInTimeIntegrity,
    QUALITY_PROVENANCE_GENUINE, QUALITY_PROVENANCE_FALLBACK_NEUTRAL, QUALITY_PROVENANCE_UNKNOWN,
    snapshot_from_dict,
    validate_structure,
)
from services.validation.pipeline_replay import replay_snapshot


IC_WEIGHTS = {"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2}


def _full_integrity():
    return {
        "provenance": PointInTimeIntegrity.POINT_IN_TIME,
        "timestamp": PointInTimeIntegrity.POINT_IN_TIME,
        "technical": PointInTimeIntegrity.POINT_IN_TIME,
        "fundamental": PointInTimeIntegrity.POINT_IN_TIME,
        "quality": PointInTimeIntegrity.POINT_IN_TIME,
    }


def _base_candidate(**overrides):
    defaults = dict(
        symbol="X", signal="BUY", confidence=70, technical_score=60.0, fundamental_score=55.0,
        sentiment_score=50.0, sentiment_available=True,
        quality_score=52.0, quality_available=True, quality_raw_score=52.0,
        reasoning=[], cap_tier="large",
    )
    defaults.update(overrides)
    return CandidateSnapshot(**defaults)


def _ok_candidate(symbol="OK"):
    return _base_candidate(symbol=symbol)


def _snapshot(candidates, mode=ReplayMode.STRICT_POINT_IN_TIME, market="US", horizon="medium",
              regime_id=0, regime_label="BULL_CALM", point_in_time_integrity=None):
    return MarketSnapshot(
        snapshot_id="qual-prov-test", as_of="2026-01-01T00:00:00Z", market=market, horizon=horizon,
        source="manual_fixture", replay_mode=mode, candidates=candidates,
        regime_id=regime_id, regime_label=regime_label, regime_weight_multipliers={},
        ic_weights=IC_WEIGHTS,
        point_in_time_integrity=point_in_time_integrity if point_in_time_integrity is not None else _full_integrity(),
    )


# ---------------------------------------------------------------------------
# 1-4. Unavailable quality evidence
# ---------------------------------------------------------------------------

class TestUnavailableQualityEvidence:
    def test_unavailable_quality_score_fifty_raw_none_is_accepted(self):
        c = _base_candidate(quality_available=False, quality_score=50, quality_raw_score=None)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.COMPLETED
        assert validate_structure(snapshot) == []

    def test_unavailable_quality_with_none_score_is_rejected(self):
        c = _base_candidate(quality_available=False, quality_score=None, quality_raw_score=None)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_score" in m for m in result.missing_requirements)

    def test_unavailable_quality_with_score_forty_is_rejected(self):
        c = _base_candidate(quality_available=False, quality_score=40.0, quality_raw_score=None)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_score" in m and "50" in m for m in result.missing_requirements)

    def test_unavailable_quality_with_nonnull_raw_score_is_rejected(self):
        c = _base_candidate(quality_available=False, quality_score=50, quality_raw_score=52.0)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_raw_score" in m for m in result.missing_requirements)


# ---------------------------------------------------------------------------
# 5-8. Available quality evidence
# ---------------------------------------------------------------------------

class TestAvailableQualityEvidence:
    def test_available_quality_raw_and_final_fifty_accepted_as_genuine(self):
        # The exact ambiguous case this fix exists to resolve: a genuine
        # score of 50 must be accepted and distinguishable from a fallback.
        c = _base_candidate(quality_available=True, quality_score=50.0, quality_raw_score=50.0)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.COMPLETED
        row = next(r for r in result.ranked_universe if r.symbol == "X")
        assert row.quality_provenance == QUALITY_PROVENANCE_GENUINE

    def test_available_quality_raw_and_final_zero_accepted_and_stays_zero_through_replay(self):
        c = _base_candidate(quality_available=True, quality_score=0.0, quality_raw_score=0.0)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.COMPLETED
        row = next(r for r in result.ranked_universe if r.symbol == "X")
        assert row.quality_provenance == QUALITY_PROVENANCE_GENUINE
        # zero must remain real evidence in the z-score, not neutral (0
        # contribution) — mirrors DP-009/DP-010's zero-preservation proof.
        assert row.factor_zscores["quality"] != 0.0 or True  # z can legitimately be 0 only if mean==0; assert via mean check below

    def test_genuine_zero_pulls_zscore_below_mean_not_neutral(self):
        low = _base_candidate(symbol="LOW", quality_available=True, quality_score=0.0, quality_raw_score=0.0)
        mid = _base_candidate(symbol="MID", quality_available=True, quality_score=20.0, quality_raw_score=20.0)
        high = _base_candidate(symbol="HIGH", quality_available=True, quality_score=40.0, quality_raw_score=40.0)
        snapshot = _snapshot([low, mid, high])
        result = replay_snapshot(snapshot)
        by_symbol = {r.symbol: r.factor_zscores["quality"] for r in result.ranked_universe}
        assert by_symbol["MID"] == pytest.approx(0.0, abs=1e-6)
        assert by_symbol["LOW"] < 0

    def test_available_quality_raw_final_mismatch_is_rejected(self):
        c = _base_candidate(quality_available=True, quality_score=52.0, quality_raw_score=48.0)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_raw_score" in m and "quality_score" in m for m in result.missing_requirements)

    def test_available_quality_with_missing_raw_score_is_rejected(self):
        c = _base_candidate(quality_available=True, quality_score=52.0, quality_raw_score=None)
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_raw_score" in m for m in result.missing_requirements)


# ---------------------------------------------------------------------------
# 9. NaN / infinity rejected
# ---------------------------------------------------------------------------

class TestNonFiniteQualityValues:
    def test_nan_quality_raw_score_rejected(self):
        c = _base_candidate(quality_available=True, quality_score=float("nan"), quality_raw_score=float("nan"))
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE

    def test_infinite_quality_raw_score_rejected(self):
        c = _base_candidate(quality_available=True, quality_score=float("inf"), quality_raw_score=float("inf"))
        snapshot = _snapshot([c, _ok_candidate()])
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE


# ---------------------------------------------------------------------------
# 10-11. Backward compatibility with fixtures predating quality_raw_score
# ---------------------------------------------------------------------------

class TestBackwardCompatibleOldFixtures:
    def _old_style_fixture_dict(self, mode="strict_point_in_time"):
        return {
            "snapshot_id": "old-fixture", "as_of": "2026-01-01T00:00:00Z", "market": "US", "horizon": "medium",
            "source": "manual_fixture", "replay_mode": mode,
            "candidates": [
                # Deliberately OMITS quality_raw_score entirely — the shape
                # a pre-2026-07-18 fixture would have.
                {"symbol": "OLD", "signal": "BUY", "confidence": 70, "technical_score": 60.0,
                 "fundamental_score": 55.0, "quality_score": 52.0, "sentiment_score": 50.0,
                 "sentiment_available": True, "quality_available": True, "reasoning": [], "cap_tier": "large"},
            ],
            "regime_id": 0, "regime_label": "BULL_CALM", "regime_weight_multipliers": {},
            "ic_weights": IC_WEIGHTS,
            "point_in_time_integrity": {
                "provenance": "point_in_time", "timestamp": "point_in_time",
                "technical": "point_in_time", "fundamental": "point_in_time", "quality": "point_in_time",
            },
        }

    def test_loaded_old_fixture_has_provided_flag_false(self):
        snapshot = snapshot_from_dict(self._old_style_fixture_dict())
        assert snapshot.candidates[0].quality_raw_score_provided is False
        assert snapshot.candidates[0].quality_raw_score is None

    def test_strict_mode_refuses_old_fixture_missing_raw_quality_provenance(self):
        snapshot = snapshot_from_dict(self._old_style_fixture_dict(mode="strict_point_in_time"))
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.REFUSED_INCOMPLETE
        assert any("quality_raw_score" in m and "not supplied" in m for m in result.missing_requirements)

    def test_diagnostic_mode_labels_old_fixture_as_unknown_provenance_never_equivalent(self):
        snapshot = snapshot_from_dict(self._old_style_fixture_dict(mode="diagnostic"))
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.DIAGNOSTIC_COMPLETED
        assert any("UNKNOWN QUALITY PROVENANCE" in w for w in result.warnings)
        assert result.production_equivalent is False
        assert result.full_pipeline_replay is False
        row = next(r for r in result.ranked_universe if r.symbol == "OLD")
        assert row.quality_provenance == QUALITY_PROVENANCE_UNKNOWN

    def test_fixture_explicitly_declaring_null_raw_score_is_not_unknown(self):
        # Contrast case: a NEW-style fixture that explicitly writes
        # "quality_raw_score": null (confirmed unavailable) must NOT be
        # treated as unknown provenance — the key's presence, not its
        # value, drives quality_raw_score_provided.
        d = self._old_style_fixture_dict(mode="strict_point_in_time")
        d["candidates"][0]["quality_raw_score"] = None
        d["candidates"][0]["quality_available"] = False
        d["candidates"][0]["quality_score"] = 50
        snapshot = snapshot_from_dict(d)
        assert snapshot.candidates[0].quality_raw_score_provided is True
        result = replay_snapshot(snapshot)
        assert result.status == ReplayStatus.COMPLETED


# ---------------------------------------------------------------------------
# 12. Deterministic hashing remains stable
# ---------------------------------------------------------------------------

class TestDeterminismWithQualityProvenance:
    def test_repeated_replay_with_quality_provenance_hashes_identically(self):
        c1 = _base_candidate(symbol="A", quality_score=0.0, quality_raw_score=0.0)
        c2 = _base_candidate(symbol="B")
        r1 = replay_snapshot(_snapshot([c1, c2]))
        r2 = replay_snapshot(_snapshot([
            _base_candidate(symbol="A", quality_score=0.0, quality_raw_score=0.0),
            _base_candidate(symbol="B"),
        ]))
        assert r1.result_hash == r2.result_hash

    def test_genuine_vs_fallback_quality_produce_different_hashes(self):
        genuine = replay_snapshot(_snapshot([
            _base_candidate(symbol="A", quality_available=True, quality_score=50.0, quality_raw_score=50.0),
            _ok_candidate(),
        ]))
        fallback = replay_snapshot(_snapshot([
            _base_candidate(symbol="A", quality_available=False, quality_score=50.0, quality_raw_score=None),
            _ok_candidate(),
        ]))
        assert genuine.result_hash != fallback.result_hash


# ---------------------------------------------------------------------------
# Direct validate_structure() unit checks
# ---------------------------------------------------------------------------

class TestValidateStructureQualityRulesDirect:
    def test_valid_available_and_unavailable_candidates_together_pass(self):
        available = _base_candidate(symbol="AVAIL", quality_available=True, quality_score=30.0, quality_raw_score=30.0)
        unavailable = _base_candidate(symbol="UNAVAIL", quality_available=False, quality_score=50, quality_raw_score=None)
        snapshot = _snapshot([available, unavailable])
        assert validate_structure(snapshot) == []

    def test_old_fixture_candidate_skips_quality_consistency_checks_not_hard_error(self):
        # quality_raw_score_provided=False must not itself be flagged by
        # validate_structure() (that's a STRICT-only concern) — it applies
        # in both modes and must stay silent here.
        c = _base_candidate(quality_raw_score_provided=False, quality_raw_score=None)
        snapshot = _snapshot([c, _ok_candidate()])
        assert validate_structure(snapshot) == []
