"""
Phase 1 spec T-1/T-4: the IntelligenceSnapshot schema itself — every
evidence state/tier/claim type is constructable, and the structures are
immutable after construction.
"""

import dataclasses
import json

import pytest

from services.research_analyst.intelligence_snapshot import (
    CLAIM_TYPES,
    EvidenceItem,
    EvidenceState,
    EvidenceTier,
    snapshot_to_dict,
)
from services.research_analyst.snapshot_builder import build_live_intelligence_snapshot
from tests.fixtures.research_analyst_fixtures import make_predict_result


def _item(state: EvidenceState, claim_type: str = "limitation", reason: str | None = "why") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"PredictionEngine:decision:x:{state.value}:{claim_type}",
        owner="PredictionEngine",
        domain="decision",
        status=state,
        tier=EvidenceTier.UNKNOWN if state is not EvidenceState.SUPPORTED else EvidenceTier.CONFIRMED,
        claim_type=claim_type,
        label="label",
        value=None,
        display_value=None,
        source_reference=None,
        as_of=None,
        captured_at="2026-07-08T10:00:00+00:00",
        market="IN",
        horizon="medium",
        provenance_ref="signal",
        reason=None if state is EvidenceState.SUPPORTED else reason,
    )


class TestSevenStatesAndTiers:
    def test_exactly_seven_states(self):
        assert {s.value for s in EvidenceState} == {
            "SUPPORTED", "MISSING", "UNAVAILABLE", "NOT_APPLICABLE",
            "FEATURE_DISABLED", "STALE", "EXECUTION_ERROR",
        }

    def test_exactly_three_tiers(self):
        assert {t.value for t in EvidenceTier} == {"CONFIRMED", "LIKELY", "UNKNOWN"}

    def test_every_state_constructable(self):
        for state in EvidenceState:
            item = _item(state)
            assert item.status is state

    def test_every_claim_type_constructable(self):
        for claim_type in CLAIM_TYPES:
            item = _item(EvidenceState.SUPPORTED, claim_type=claim_type)
            assert item.claim_type == claim_type


class TestImmutability:
    def test_evidence_item_frozen(self):
        item = _item(EvidenceState.SUPPORTED)
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.value = 99

    def test_snapshot_frozen(self):
        snap = build_live_intelligence_snapshot(
            make_predict_result(), symbol="RELIANCE", market="IN", horizon="medium")
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.snapshot_hash = "tampered"
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.scope.symbol = "OTHER"

    def test_nested_mappings_reject_item_assignment(self):
        snap = build_live_intelligence_snapshot(
            make_predict_result(), symbol="RELIANCE", market="IN", horizon="medium")
        with pytest.raises(TypeError):
            snap.decision_context["signal"] = "SELL"
        with pytest.raises(TypeError):
            snap.engine_outputs["business_quality"]["score"] = 0
        with pytest.raises(TypeError):
            snap.availability.owner_statuses["PredictionEngine"] = "UNAVAILABLE"

    def test_evidence_items_and_disclosures_are_tuples(self):
        snap = build_live_intelligence_snapshot(
            make_predict_result(), symbol="RELIANCE", market="IN", horizon="medium")
        assert isinstance(snap.evidence_items, tuple)
        assert isinstance(snap.disclosures, tuple)


class TestSerialization:
    def test_snapshot_to_dict_is_json_serializable(self):
        snap = build_live_intelligence_snapshot(
            make_predict_result(market="US", symbol="AAPL"),
            symbol="AAPL", market="US", horizon="medium")
        as_dict = snapshot_to_dict(snap)
        text = json.dumps(as_dict, sort_keys=True)
        assert "snapshot_hash" in as_dict and snap.snapshot_hash in text

    def test_include_hash_false_omits_only_hash(self):
        snap = build_live_intelligence_snapshot(
            make_predict_result(), symbol="RELIANCE", market="IN", horizon="medium")
        with_hash = snapshot_to_dict(snap, include_hash=True)
        without = snapshot_to_dict(snap, include_hash=False)
        assert "snapshot_hash" in with_hash and "snapshot_hash" not in without
        with_hash.pop("snapshot_hash")
        assert with_hash == without
