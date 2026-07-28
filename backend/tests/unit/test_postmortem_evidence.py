"""
Unit tests for services/postmortem/evidence.py — the Trade Postmortem
Engine's Sprint 1 shared evidence/claim provenance model. No DB, no HTTP.
"""
import pytest

from services.postmortem.evidence import (
    INSUFFICIENT_EVIDENCE_SENTENCE,
    ConfidenceBand,
    EvidenceClass,
    PostmortemClaim,
    RuleDefinition,
    insufficient_evidence_claim,
    make_claim_id,
    make_evidence_id,
    register_rule,
)


def _valid_claim(**overrides) -> dict:
    base = dict(
        claim_id="CLM-1-TEST_RULE_001", report_section="test", factor="test_factor",
        claim_text="A supported claim.", evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value,
        confidence_band=ConfidenceBand.MODERATE.value, supporting_evidence_ids=["EV-1-a-b"],
        opposing_evidence_ids=[], missing_evidence=[], contradiction_flags=[],
        rule_id="TEST_RULE_001", rule_version="1.0.0",
    )
    base.update(overrides)
    return base


class TestClaimValidation:
    def test_valid_supported_claim_constructs(self):
        claim = PostmortemClaim(**_valid_claim())
        assert claim.claim_id == "CLM-1-TEST_RULE_001"

    def test_non_insufficient_claim_without_supporting_evidence_rejected(self):
        with pytest.raises(ValueError, match="supporting evidence"):
            PostmortemClaim(**_valid_claim(supporting_evidence_ids=[]))

    def test_insufficient_evidence_claim_with_wrong_text_rejected(self):
        with pytest.raises(ValueError, match="exact fallback sentence"):
            PostmortemClaim(**_valid_claim(
                evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value,
                confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
                claim_text="We don't really know.", supporting_evidence_ids=[],
            ))

    def test_insufficient_evidence_claim_with_wrong_confidence_rejected(self):
        with pytest.raises(ValueError, match="NOT_ASSESSABLE"):
            PostmortemClaim(**_valid_claim(
                evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value,
                confidence_band=ConfidenceBand.LOW.value,
                claim_text=INSUFFICIENT_EVIDENCE_SENTENCE, supporting_evidence_ids=[],
            ))

    def test_insufficient_evidence_claim_with_supporting_evidence_rejected(self):
        with pytest.raises(ValueError, match="must not cite supporting evidence"):
            PostmortemClaim(**_valid_claim(
                evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value,
                confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
                claim_text=INSUFFICIENT_EVIDENCE_SENTENCE, supporting_evidence_ids=["EV-1-a-b"],
            ))

    def test_conflicting_evidence_without_opposing_ids_rejected(self):
        with pytest.raises(ValueError, match="opposing evidence"):
            PostmortemClaim(**_valid_claim(
                evidence_class=EvidenceClass.CONFLICTING_EVIDENCE.value, opposing_evidence_ids=[],
            ))

    def test_conflicting_evidence_with_both_ids_constructs(self):
        claim = PostmortemClaim(**_valid_claim(
            evidence_class=EvidenceClass.CONFLICTING_EVIDENCE.value, opposing_evidence_ids=["EV-1-c-d"],
        ))
        assert claim.opposing_evidence_ids == ["EV-1-c-d"]

    def test_missing_rule_id_rejected(self):
        with pytest.raises(ValueError, match="rule_id"):
            PostmortemClaim(**_valid_claim(rule_id=""))

    def test_missing_rule_version_rejected(self):
        with pytest.raises(ValueError, match="rule_id"):
            PostmortemClaim(**_valid_claim(rule_version=""))


class TestInsufficientEvidenceHelper:
    def test_produces_exact_sentence_and_not_assessable(self):
        claim = insufficient_evidence_claim(
            claim_id="CLM-1-X", report_section="s", factor="f",
            rule_id="X", rule_version="1.0.0", missing_evidence=["nothing captured"],
        )
        assert claim.claim_text == INSUFFICIENT_EVIDENCE_SENTENCE
        assert claim.confidence_band == ConfidenceBand.NOT_ASSESSABLE.value
        assert claim.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value
        assert claim.supporting_evidence_ids == []


class TestDeterministicIds:
    def test_evidence_id_deterministic_for_same_inputs(self):
        assert make_evidence_id(1, "entry_snapshot", "technical_signal") == make_evidence_id(1, "entry_snapshot", "technical_signal")

    def test_evidence_id_differs_for_different_trade(self):
        assert make_evidence_id(1, "entry_snapshot", "technical_signal") != make_evidence_id(2, "entry_snapshot", "technical_signal")

    def test_claim_id_deterministic_for_same_inputs(self):
        assert make_claim_id(5, "RULE_001") == make_claim_id(5, "RULE_001")

    def test_claim_id_never_uses_random_source(self):
        """No uuid4/random import anywhere in evidence.py — grepped directly
        against actual import/call statements, not the docstring prose
        (which itself discusses why uuid4 is forbidden)."""
        import ast
        import inspect
        import services.postmortem.evidence as mod
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [n.name for n in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
                assert "uuid" not in names, "evidence.py must never import uuid"
                assert "random" not in names, "evidence.py must never import random"


class TestRuleRegistry:
    def test_register_rule_adds_to_registry(self):
        from services.postmortem.evidence import RULE_REGISTRY
        rule = RuleDefinition(
            rule_id="TEST_REGISTRY_ONLY_001", rule_version="1.0.0", report_section="test",
            required_evidence=(), optional_evidence=(), supporting_condition="x",
            opposing_condition="y", insufficient_evidence_condition="z", permitted_output_classes=(),
        )
        register_rule(rule)
        assert RULE_REGISTRY["TEST_REGISTRY_ONLY_001"] is rule

    def test_duplicate_rule_id_rejected(self):
        rule = RuleDefinition(
            rule_id="TEST_DUP_001", rule_version="1.0.0", report_section="test",
            required_evidence=(), optional_evidence=(), supporting_condition="x",
            opposing_condition="y", insufficient_evidence_condition="z", permitted_output_classes=(),
        )
        register_rule(rule)
        with pytest.raises(ValueError, match="duplicate"):
            register_rule(rule)
