"""
Phase 1 spec T-13: the ContractValidator — one dedicated rejecting case
per rule V-1…V-10, plus fully-valid snapshots accepted. Tampered
snapshots are re-hashed where a rule other than V-7 is the one under
test, so each rule is isolated.
"""

import dataclasses

from services.research_analyst.contract_validator import validate_snapshot
from services.research_analyst.intelligence_snapshot import (
    EvidenceItem,
    EvidenceState,
    EvidenceTier,
    compute_snapshot_hash,
    deep_freeze,
)
from services.research_analyst.snapshot_builder import build_live_intelligence_snapshot
from tests.fixtures.research_analyst_fixtures import make_predict_result


def _snap(market="IN", symbol="RELIANCE", horizon="medium", fixture=None):
    return build_live_intelligence_snapshot(
        fixture or make_predict_result(market=market, symbol=symbol, horizon=horizon),
        symbol=symbol, market=market, horizon=horizon)


def _rehash(snap):
    return dataclasses.replace(snap, snapshot_hash=compute_snapshot_hash(snap))


def _tamper(snap, **fields):
    """Replace fields then re-stamp a correct hash so V-7 stays quiet and
    the rule under test is isolated."""
    return _rehash(dataclasses.replace(snap, **fields))


def _rule_ids(result):
    return {v.rule_id for v in result.violations}


def _extra_item(**overrides) -> EvidenceItem:
    base = dict(
        evidence_id="PredictionEngine:decision:extra",
        owner="PredictionEngine",
        domain="decision",
        status=EvidenceState.SUPPORTED,
        tier=EvidenceTier.CONFIRMED,
        claim_type="fact",
        label="extra",
        value=1.0,
        display_value="1.0",
        source_reference=None,
        as_of=None,
        captured_at="2026-07-08T10:00:00+00:00",
        market="IN",
        horizon="medium",
        provenance_ref="signal",
        reason=None,
    )
    base.update(overrides)
    return EvidenceItem(**base)


class TestValidSnapshotsAccepted:
    def test_full_in_and_us_snapshots_valid(self):
        for market, symbol in (("IN", "RELIANCE"), ("US", "AAPL")):
            result = validate_snapshot(_snap(market=market, symbol=symbol))
            assert result.valid, result.violations

    def test_degraded_but_honest_snapshot_valid(self):
        fixture = make_predict_result()
        fixture["business_quality"] = None
        result = validate_snapshot(_snap(fixture=fixture))
        assert result.valid, result.violations


class TestRejectionRules:
    def test_v1_missing_generated_at(self):
        fixture = make_predict_result()
        del fixture["generated_at"]
        result = validate_snapshot(_snap(fixture=fixture))
        assert not result.valid and "V-1" in _rule_ids(result)

    def test_v2_reserved_horizon_rejected_not_mapped(self):
        snap = _snap()
        bad = _tamper(snap, scope=dataclasses.replace(snap.scope, horizon="investment"))
        result = validate_snapshot(bad)
        assert "V-2" in _rule_ids(result)
        # every item's horizon now also mismatches the scope → V-6 too
        assert "V-6" in _rule_ids(result)

    def test_v2_non_live_snapshot_type_rejected(self):
        result = validate_snapshot(_tamper(_snap(), snapshot_type="persisted"))
        assert "V-2" in _rule_ids(result)

    def test_v3_duplicate_evidence_id(self):
        snap = _snap()
        dup = snap.evidence_items[0]
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (dup,)))
        assert "V-3" in _rule_ids(result)

    def test_v3_missing_owner(self):
        snap = _snap()
        bad_item = _extra_item(owner="", evidence_id="::x")
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-3" in _rule_ids(result)

    def test_v4_fact_with_untyped_value(self):
        snap = _snap()
        bad_item = _extra_item(value={"nested": "dict"})
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-4" in _rule_ids(result)

    def test_v5_non_supported_without_reason(self):
        snap = _snap()
        bad_item = _extra_item(evidence_id="PredictionEngine:decision:noreason",
                               status=EvidenceState.MISSING, tier=EvidenceTier.UNKNOWN,
                               claim_type="limitation", value=None, reason=None)
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-5" in _rule_ids(result)

    def test_v6_cross_market_item(self):
        snap = _snap(market="IN")
        bad_item = _extra_item(evidence_id="PredictionEngine:decision:crossmkt", market="US")
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-6" in _rule_ids(result)

    def test_v6_cross_horizon_item(self):
        snap = _snap(horizon="medium")
        bad_item = _extra_item(evidence_id="PredictionEngine:decision:crosshz", horizon="long")
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-6" in _rule_ids(result)

    def test_v7_tampered_content_without_rehash(self):
        snap = _snap()
        tampered = dataclasses.replace(snap, snapshot_id=snap.snapshot_id + ":x")
        result = validate_snapshot(tampered)
        assert "V-7" in _rule_ids(result)

    def test_v7_malformed_hash(self):
        result = validate_snapshot(dataclasses.replace(_snap(), snapshot_hash="nothex"))
        assert "V-7" in _rule_ids(result)

    def test_v8_user_context_in_live_snapshot(self):
        snap = _snap()
        result = validate_snapshot(
            _tamper(snap, consumer_context=deep_freeze({"user_id": "u-1", "nested": {"holdings": []}})))
        ids = _rule_ids(result)
        assert "V-8" in ids
        reasons = " ".join(v.reason for v in result.violations if v.rule_id == "V-8")
        assert "user_id" in reasons and "holdings" in reasons

    def test_v9_unregistered_owner(self):
        snap = _snap()
        bad_item = _extra_item(evidence_id="RogueOwner:decision:x", owner="RogueOwner")
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-9" in _rule_ids(result)

    def test_v9_owner_outside_its_domain(self):
        snap = _snap()
        bad_item = _extra_item(evidence_id="MarketRegime:decision:x", owner="MarketRegime",
                               horizon="not_applicable")
        result = validate_snapshot(_tamper(snap, evidence_items=snap.evidence_items + (bad_item,)))
        assert "V-9" in _rule_ids(result)

    def test_v10_owner_status_inconsistent_with_items(self):
        snap = _snap()
        statuses = dict(snap.availability.owner_statuses)
        statuses["PredictionEngine"] = "UNAVAILABLE"  # but its items are SUPPORTED
        result = validate_snapshot(
            _tamper(snap, availability=dataclasses.replace(
                snap.availability, owner_statuses=deep_freeze(statuses))))
        assert "V-10" in _rule_ids(result)

    def test_v10_mapped_owner_with_no_items(self):
        snap = _snap()
        statuses = dict(snap.availability.owner_statuses)
        statuses["GhostOwner"] = "SUPPORTED"
        result = validate_snapshot(
            _tamper(snap, availability=dataclasses.replace(
                snap.availability, owner_statuses=deep_freeze(statuses))))
        assert "V-10" in _rule_ids(result)

    def test_rejection_reports_rule_id_and_reason_never_repairs(self):
        fixture = make_predict_result()
        del fixture["generated_at"]
        snap = _snap(fixture=fixture)
        result = validate_snapshot(snap)
        assert not result.valid
        assert all(v.rule_id and v.reason for v in result.violations)
        # the snapshot itself is untouched — validator never repairs
        assert snap.timestamps.generated_at is None
