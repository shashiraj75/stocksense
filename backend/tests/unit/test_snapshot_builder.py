"""
Phase 1 spec T-2/T-3/T-5/T-6/T-7/T-8/T-9: the snapshot builder — value
mapping, input immutability, identity disjointness, degraded inputs,
the legacy Daily Picks firewall, scope rejection, and hash determinism.
"""

import json

import pytest

from services.research_analyst.intelligence_snapshot import (
    EvidenceState,
    canonical_serialization,
    snapshot_to_dict,
)
from services.research_analyst.snapshot_builder import (
    ScopeMismatchError,
    UnsupportedScopeError,
    build_live_intelligence_snapshot,
)
from tests.fixtures.research_analyst_fixtures import (
    deep_snapshot_of,
    make_legacy_daily_picks_shaped,
    make_predict_result,
)


def _build(fixture, symbol=None, market=None, horizon=None):
    return build_live_intelligence_snapshot(
        fixture,
        symbol=symbol or fixture.get("symbol", "RELIANCE"),
        market=market or fixture.get("market", "IN"),
        horizon=horizon or fixture.get("horizon", "medium"),
    )


def _items_of(snap, owner):
    return [i for i in snap.evidence_items if i.owner == owner]


class TestHappyPath:
    @pytest.mark.parametrize("market,symbol", [("IN", "RELIANCE"), ("US", "AAPL")])
    @pytest.mark.parametrize("horizon", ["short", "medium", "long"])
    def test_both_markets_all_horizons(self, market, symbol, horizon):
        snap = _build(make_predict_result(market=market, symbol=symbol, horizon=horizon))
        assert snap.scope.market == market and snap.scope.horizon == horizon
        assert snap.availability.overall_status == ("PARTIAL" if market == "IN" else "COMPLETE")
        assert snap.availability.owner_statuses["PredictionEngine"] == "SUPPORTED"

    def test_market_currency_and_exchange_separation(self):
        snap_in = _build(make_predict_result(market="IN"))
        snap_us = _build(make_predict_result(market="US", symbol="AAPL"))
        assert (snap_in.scope.currency, snap_in.scope.exchange) == ("INR", "NSE")
        assert (snap_us.scope.currency, snap_us.scope.exchange) == ("USD", "US-composite")

    def test_decision_values_survive_copy_exactly(self):
        fixture = make_predict_result()
        snap = _build(fixture)
        assert snap.decision_context["signal"] == "BUY"
        assert snap.decision_context["confidence"] == 68
        assert dict(snap.decision_context["trade_levels"]) == fixture["trade_levels"]
        signal_item = next(i for i in snap.evidence_items
                           if i.evidence_id == "PredictionEngine:decision:signal")
        assert signal_item.value == "BUY" and signal_item.status is EvidenceState.SUPPORTED
        assert signal_item.as_of == fixture["data_timestamp"]

    def test_owner_supplied_timestamps_never_substituted(self):
        snap = _build(make_predict_result())
        assert snap.timestamps.generated_at == "2026-07-08T10:00:00+00:00"
        assert snap.timestamps.data_as_of == "2026-07-07T00:00:00"
        fixture = make_predict_result()
        del fixture["generated_at"]
        snap2 = _build(fixture)
        assert snap2.timestamps.generated_at is None  # V-1 territory, not now()

    def test_bull_bear_cases_mapped_with_claim_types(self):
        snap = _build(make_predict_result())
        bull = [i for i in snap.evidence_items if i.evidence_id.startswith("PredictionEngine:decision:bull:")]
        bear = [i for i in snap.evidence_items if i.evidence_id.startswith("PredictionEngine:risk:bear:")]
        assert len(bull) == 2 and all(i.claim_type == "owner_conclusion" for i in bull)
        assert len(bear) == 1 and bear[0].claim_type == "counter_evidence" and bear[0].domain == "risk"


class TestInputImmutabilityAndIdentity:
    def test_input_dict_not_mutated(self):
        fixture = make_predict_result(market="US", symbol="AAPL")
        before = deep_snapshot_of(fixture)
        _build(fixture)
        assert fixture == before

    def test_zero_shared_identity_with_input(self):
        fixture = make_predict_result(market="US", symbol="AAPL")
        snap = _build(fixture)
        assert snap.engine_outputs["business_quality"] is not fixture["business_quality"]
        assert snap.decision_context["trade_levels"] is not fixture["trade_levels"]
        assert snap.market_context["market_regime"] is not fixture["market_regime"]
        # Mutating the source after build cannot alter the snapshot.
        fixture["business_quality"]["score"] = -1
        fixture["trade_levels"]["stop_loss"] = 0
        assert snap.engine_outputs["business_quality"]["score"] == 72.5
        assert snap.decision_context["trade_levels"]["stop_loss"] == 2755.0


class TestDegradedInputs:
    def test_missing_engine_key(self):
        fixture = make_predict_result()
        del fixture["growth_intelligence"]
        snap = _build(fixture)
        assert snap.availability.owner_statuses["GrowthIntelligence"] == "MISSING"
        (item,) = _items_of(snap, "GrowthIntelligence")
        assert item.status is EvidenceState.MISSING and item.reason

    def test_none_engine_is_unavailable(self):
        fixture = make_predict_result()
        fixture["business_quality"] = None
        snap = _build(fixture)
        assert snap.availability.owner_statuses["BusinessQuality"] == "UNAVAILABLE"

    def test_financial_strength_india_is_not_applicable_with_structural_reason(self):
        snap = _build(make_predict_result(market="IN"))
        assert snap.availability.owner_statuses["FinancialStrength"] == "NOT_APPLICABLE"
        (item,) = _items_of(snap, "FinancialStrength")
        assert "India" in item.reason and "market-structural" in item.reason

    def test_owner_error_shape_is_execution_error_with_bounded_reason(self):
        fixture = make_predict_result()
        fixture["valuation_intelligence"] = {"error": "Traceback: secret internals blew up"}
        snap = _build(fixture)
        assert snap.availability.owner_statuses["ValuationIntelligence"] == "EXECUTION_ERROR"
        (item,) = _items_of(snap, "ValuationIntelligence")
        assert "secret internals" not in (item.reason or "")

    def test_feature_disabled_marker(self):
        fixture = make_predict_result()
        fixture["valuation_intelligence"] = {"status": "feature_disabled"}
        snap = _build(fixture)
        assert snap.availability.owner_statuses["ValuationIntelligence"] == "FEATURE_DISABLED"

    def test_error_result_is_unavailable_overall(self):
        snap = _build({"symbol": "RELIANCE", "market": "IN", "horizon": "medium",
                       "error": "Data fetch timed out"})
        assert snap.availability.overall_status == "UNAVAILABLE"
        assert snap.availability.owner_statuses["PredictionEngine"] == "UNAVAILABLE"

    def test_every_non_supported_item_has_reason_and_stale_never_emitted(self):
        fixtures = [
            make_predict_result(market="IN"),
            make_legacy_daily_picks_shaped(),
            {"symbol": "X", "market": "IN", "horizon": "short", "error": "boom"},
        ]
        broken = make_predict_result()
        broken["business_quality"] = None
        broken["growth_intelligence"] = {"error": "x"}
        del broken["market_regime"]
        fixtures.append(broken)
        for fixture in fixtures:
            snap = _build(fixture, symbol=fixture.get("symbol", "X"),
                          market=fixture.get("market", "IN"),
                          horizon=fixture.get("horizon", "medium"))
            for item in snap.evidence_items:
                assert item.status is not EvidenceState.STALE
                if item.status is not EvidenceState.SUPPORTED:
                    assert item.reason and item.reason.strip()


class TestLegacyFirewall:
    def test_legacy_scores_never_become_modern_evidence(self):
        snap = _build(make_legacy_daily_picks_shaped())
        assert snap.availability.owner_statuses["GrowthIntelligence"] == "MISSING"
        assert snap.availability.owner_statuses["ValuationIntelligence"] == "MISSING"
        for owner in ("GrowthIntelligence", "ValuationIntelligence"):
            for item in _items_of(snap, owner):
                assert item.status is not EvidenceState.SUPPORTED
                assert item.value not in (55.0, 60.0)
        text = canonical_serialization(snapshot_to_dict(snap))
        assert "growth_score" not in text and "valuation_score" not in text


class TestScopeRejection:
    def test_unsupported_horizon_rejected_not_mapped(self):
        fixture = make_predict_result()
        del fixture["horizon"]  # isolate: no mismatch, purely the vocabulary check
        for horizon in ("investment", "weekly", "", "LONG"):
            with pytest.raises(UnsupportedScopeError):
                build_live_intelligence_snapshot(
                    fixture, symbol="RELIANCE", market="IN", horizon=horizon)

    def test_unsupported_market_rejected(self):
        with pytest.raises(UnsupportedScopeError):
            build_live_intelligence_snapshot(make_predict_result(), symbol="X", market="UK", horizon="medium")

    def test_scope_mismatch_rejected(self):
        fixture = make_predict_result(market="IN", symbol="RELIANCE")
        with pytest.raises(ScopeMismatchError):
            build_live_intelligence_snapshot(fixture, symbol="TCS", market="IN", horizon="medium")
        with pytest.raises(ScopeMismatchError):
            build_live_intelligence_snapshot(fixture, symbol="RELIANCE", market="US", horizon="medium")
        with pytest.raises(ScopeMismatchError):
            build_live_intelligence_snapshot(fixture, symbol="RELIANCE", market="IN", horizon="long")

    def test_non_dict_input_rejected(self):
        with pytest.raises(TypeError):
            build_live_intelligence_snapshot(None, symbol="X", market="IN", horizon="medium")


class TestDeterminism:
    def test_identical_input_identical_hash(self):
        a = _build(make_predict_result())
        b = _build(make_predict_result())
        assert a.snapshot_hash == b.snapshot_hash
        assert (canonical_serialization(snapshot_to_dict(a))
                == canonical_serialization(snapshot_to_dict(b)))

    def test_any_field_change_changes_hash(self):
        base = _build(make_predict_result())
        changed_fixture = make_predict_result()
        changed_fixture["confidence"] = 69
        changed = _build(changed_fixture)
        assert base.snapshot_hash != changed.snapshot_hash

    def test_hash_is_json_stable(self):
        snap = _build(make_predict_result())
        payload = snapshot_to_dict(snap, include_hash=False)
        json.loads(canonical_serialization(payload))  # round-trips cleanly
