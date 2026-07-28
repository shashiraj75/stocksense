"""
Evidence and claim provenance model — Trade Postmortem Engine, Sprint 1.

Problem this solves: the Stage-0 prototype (services/postmortem/
causal_analysis.py) produced narrative conclusions with no way for a
caller to check what evidence backed them, whether the claim's evidence
class matched its actual support, or whether the sentence used for
"we don't know" was consistent. This module is the shared, versioned
foundation every later attribution module builds claims on top of —
introduced once here so every future postmortem rule reuses the same
evidence class / confidence band vocabulary and the same fallback
sentence, rather than each module inventing its own.

Non-negotiable fallback (verbatim, product-owner mandated): whenever
evidence is missing, stale, contradictory, incomparable or insufficient,
every consumer of this module must use INSUFFICIENT_EVIDENCE_SENTENCE
exactly, never a paraphrase — see test_evidence_fallback_sentence.py for
the regression test proving there is exactly one definition and no
inconsistent copy anywhere in postmortem output.

Determinism: `evidence_id`/`claim_id` are built from stable, already-known
inputs (trade_id + category/name or trade_id + rule_id) — never
`uuid.uuid4()` or any other non-reproducible source. The same trade run
through the same rules must always produce byte-identical IDs, so a
report can be regenerated and diffed, not just recomputed and trusted.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

EVIDENCE_MODEL_VERSION = "1.0.0"

# The one and only spelling of the fallback sentence. Every module that
# needs to say "we don't know" imports this constant — never retypes the
# string. test_evidence_fallback_sentence.py greps the whole postmortem
# package for the literal English fallback phrase and asserts every match
# is exactly this constant, so an inconsistent paraphrase anywhere in this
# package fails CI, not just a code review.
INSUFFICIENT_EVIDENCE_SENTENCE = "Insufficient evidence to determine this factor reliably."


class SourceType(str, Enum):
    SERVER_STORED = "SERVER_STORED"
    SERVER_DERIVED = "SERVER_DERIVED"
    CLIENT_REPORTED = "CLIENT_REPORTED"
    APPROVED_EXTERNAL_SOURCE = "APPROVED_EXTERNAL_SOURCE"
    # A third-party market-data dependency used only for bounded,
    # replayable evidence acquisition — NOT a vetted/licensed/production-
    # authoritative source. Distinct from APPROVED_EXTERNAL_SOURCE, which
    # implies an actual approval review this codebase has not performed
    # for any provider. See price_path_acquisition.py's module docstring.
    EXTERNAL_UNOFFICIAL_DAILY = "EXTERNAL_UNOFFICIAL_DAILY"
    UNAVAILABLE = "UNAVAILABLE"


class EvidenceVerificationLevel(str, Enum):
    """Distinct from entry_snapshot.VerificationLevel (a narrower,
    Buy-time-only concept: SERVER_VERIFIED/CLIENT_REPORTED/UNAVAILABLE).
    This is the wider vocabulary evidence items across the whole
    postmortem report use, including the two levels entry_snapshot's
    trust model never produces today (MECHANICALLY_VERIFIED,
    DIRECTLY_OBSERVED) — kept as a separate type rather than widening
    entry_snapshot's own enum, since that module's docstring is explicit
    that it never asserts SERVER_VERIFIED and this module must not
    silently loosen that guarantee for existing Buy-time evidence."""

    MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"
    DIRECTLY_OBSERVED = "DIRECTLY_OBSERVED"
    CLIENT_REPORTED = "CLIENT_REPORTED"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class FreshnessStatus(str, Enum):
    POINT_IN_TIME_VALID = "POINT_IN_TIME_VALID"
    STALE = "STALE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class EvidenceClass(str, Enum):
    MECHANICALLY_VERIFIED = "MECHANICALLY_VERIFIED"
    DIRECTLY_OBSERVED = "DIRECTLY_OBSERVED"
    EVIDENCE_SUPPORTED = "EVIDENCE_SUPPORTED"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ConfidenceBand(str, Enum):
    """Not a statistical probability — a qualitative label for how much
    the cited evidence supports the claim text, nothing more. Never
    render this as a percentage; see the frontend's own rule against
    that in postmortem/page.tsx."""

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    category: str
    name: str
    value: object
    units: str | None
    observation_timestamp: datetime | None
    source: str
    source_type: str
    verification_level: str
    freshness_status: str
    limitations: list[str] = field(default_factory=list)


def make_evidence_id(trade_id: int, category: str, name: str) -> str:
    """Deterministic, human-readable — never a random UUID (see module
    docstring). Stable for the same (trade_id, category, name) triple
    regardless of when or how many times the report is regenerated."""
    return f"EV-{trade_id}-{category}-{name}"


@dataclass(frozen=True)
class PostmortemClaim:
    """A single, independently-checkable narrative conclusion. Every
    non-trivial sentence a postmortem report makes about a trade must be
    represented by one of these — never free text with no evidence trail
    behind it. Validated in `__post_init__` so an invalid combination
    (e.g. an INSUFFICIENT_EVIDENCE claim that still cites supporting
    evidence) fails at construction time, not silently in a test months
    later."""

    claim_id: str
    report_section: str
    factor: str
    claim_text: str
    evidence_class: str
    confidence_band: str
    supporting_evidence_ids: list[str]
    opposing_evidence_ids: list[str]
    missing_evidence: list[str]
    contradiction_flags: list[str]
    rule_id: str
    rule_version: str
    limitations: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value:
            if self.claim_text != INSUFFICIENT_EVIDENCE_SENTENCE:
                raise ValueError(
                    f"claim {self.claim_id}: INSUFFICIENT_EVIDENCE claims must use the exact fallback "
                    f"sentence, got: {self.claim_text!r}"
                )
            if self.confidence_band != ConfidenceBand.NOT_ASSESSABLE.value:
                raise ValueError(
                    f"claim {self.claim_id}: INSUFFICIENT_EVIDENCE claims must use NOT_ASSESSABLE confidence, "
                    f"got: {self.confidence_band!r}"
                )
            if self.supporting_evidence_ids:
                raise ValueError(
                    f"claim {self.claim_id}: INSUFFICIENT_EVIDENCE claims must not cite supporting evidence "
                    "(that would contradict the 'insufficient' label)"
                )
        else:
            if not self.supporting_evidence_ids:
                raise ValueError(
                    f"claim {self.claim_id}: every claim except INSUFFICIENT_EVIDENCE must cite at least one "
                    "supporting evidence ID"
                )

        if self.evidence_class == EvidenceClass.CONFLICTING_EVIDENCE.value:
            if not self.opposing_evidence_ids:
                raise ValueError(
                    f"claim {self.claim_id}: CONFLICTING_EVIDENCE claims must cite both supporting and "
                    "opposing evidence"
                )

        if not self.rule_id or not self.rule_version:
            raise ValueError(f"claim {self.claim_id}: rule_id and rule_version are required")


def make_claim_id(trade_id: int, rule_id: str) -> str:
    """One rule fires at most once per trade in this module's design, so
    (trade_id, rule_id) is already a stable, collision-free key — no
    counter or random suffix needed."""
    return f"CLM-{trade_id}-{rule_id}"


def insufficient_evidence_claim(
    *, claim_id: str, report_section: str, factor: str, rule_id: str, rule_version: str,
    missing_evidence: list[str], limitations: list[str] | None = None,
) -> PostmortemClaim:
    """The single construction path for an INSUFFICIENT_EVIDENCE claim —
    every caller goes through this instead of hand-building the dataclass,
    so the exact sentence and NOT_ASSESSABLE confidence can never drift
    per call site."""
    return PostmortemClaim(
        claim_id=claim_id,
        report_section=report_section,
        factor=factor,
        claim_text=INSUFFICIENT_EVIDENCE_SENTENCE,
        evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value,
        confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
        supporting_evidence_ids=[],
        opposing_evidence_ids=[],
        missing_evidence=missing_evidence,
        contradiction_flags=[],
        rule_id=rule_id,
        rule_version=rule_version,
        limitations=limitations or [],
    )


@dataclass(frozen=True)
class RuleDefinition:
    """Documents one deterministic rule's identity and evidence contract.
    The rule's actual logic still lives in Python (this sprint keeps
    implementation in evidence_attribution.py, per the sprint brief's
    'may still be implemented in Python' allowance) — this registry entry
    is what makes the rule's identity, required evidence and permitted
    outputs explicit and independently testable, instead of hidden inside
    an unexplained if/elif chain."""

    rule_id: str
    rule_version: str
    report_section: str
    required_evidence: tuple[str, ...]
    optional_evidence: tuple[str, ...]
    supporting_condition: str
    opposing_condition: str
    insufficient_evidence_condition: str
    permitted_output_classes: tuple[str, ...]


RULE_REGISTRY: dict[str, RuleDefinition] = {}


def register_rule(rule: RuleDefinition) -> RuleDefinition:
    if rule.rule_id in RULE_REGISTRY:
        raise ValueError(f"duplicate rule_id registered: {rule.rule_id}")
    RULE_REGISTRY[rule.rule_id] = rule
    return rule
