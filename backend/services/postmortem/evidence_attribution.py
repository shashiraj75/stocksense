"""
Evidence attribution engine — Trade Postmortem Engine, Sprint 1.

Replaces services/postmortem/causal_analysis.py (Stage 0 prototype),
which is deleted by this sprint. The prototype's central defect, found
during the Sprint 1 audit: its AGREED/CONTRADICTED signal labels were
derived by comparing a signal's entry-time direction against the trade's
OWN FINAL P&L SIGN — meaning a winning trade with a bullish entry signal
was unconditionally reported as proof "the signal worked," and a losing
trade with the same signal as proof it "failed." That is outcome-circular
reasoning: the outcome was used as its own evidence. The old root-cause
table then always emitted exactly one category (first-match-wins),
which forced a single explanation even when no evidence justified
picking one over another.

This module replaces both mechanisms:

- Signal "effectiveness" now requires an independent, LATER, comparable
  value to score against (see SignalStatus below). This prototype's data
  model has no later-signal-recapture and no price-path evidence yet
  (that is a future, separately-scoped sprint) — so every signal
  currently and correctly resolves to NOT_TESTABLE (a captured entry
  fact exists, but nothing to compare it against) or
  INSUFFICIENT_EVIDENCE (not even the entry fact exists). Never
  WORKED_AS_EXPECTED/INVALIDATED from this module today — that would
  require evidence this codebase does not yet capture.

- "Root cause" is replaced by a multi-contributor model
  (ContributorAssessment) that returns zero, one, or many categories,
  each independently evidenced, never forced. `primary_contributor` is
  populated only when exactly one category is STRONGLY_SUPPORTED with no
  competing SUPPORTED/CONFLICTED category — given this sprint's still-
  narrow evidence base, that bar is rarely met, which is intentional: a
  null primary contributor is a more honest report than a confident-
  sounding guess.

Every non-trivial narrative statement here is represented by a
PostmortemClaim (services/postmortem/evidence.py) with explicit
supporting/opposing evidence IDs, a rule ID/version, and a confidence
band — never a bare string with no evidence trail.

Pure function, no I/O: identical to the module it replaces, this never
queries a database, calls an external API, or calls an LLM.
"""

import math
from dataclasses import dataclass, field
from enum import Enum

from services.postmortem.deterministic import DeterministicPostmortem, EvidenceCompleteness, ExitMechanism, Outcome
from services.postmortem.entry_snapshot import EntrySnapshot
from services.postmortem.evidence import (
    RULE_REGISTRY,
    ConfidenceBand,
    EvidenceClass,
    EvidenceItem,
    EvidenceVerificationLevel,
    FreshnessStatus,
    PostmortemClaim,
    RuleDefinition,
    SourceType,
    insufficient_evidence_claim,
    make_claim_id,
    make_evidence_id,
    register_rule,
)

# Bumped whenever the RULES in this module change — independent of
# evidence.EVIDENCE_MODEL_VERSION (the shared evidence/claim contract's
# own version) and of deterministic.CALCULATION_VERSION (Phase 1's
# unrelated financial-calculation rules).
ATTRIBUTION_RULES_VERSION = "2.0.0"

_MISSING_LATER_EVIDENCE = [
    "no timestamped later-than-entry comparable value is captured by this codebase yet "
    "(price-path/signal-recapture evidence acquisition is a later, separately-scoped sprint)"
]
_MISSING_MARKET_DATA = [
    "no benchmark/sector/volatility/liquidity/news evidence acquisition exists in this codebase yet "
    "(a later, separately-scoped sprint)"
]


class SignalStatus(str, Enum):
    WORKED_AS_EXPECTED = "WORKED_AS_EXPECTED"
    PARTIALLY_WORKED = "PARTIALLY_WORKED"
    WEAKENED = "WEAKENED"
    REVERSED = "REVERSED"
    INVALIDATED = "INVALIDATED"
    NOT_TESTABLE = "NOT_TESTABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ThesisVerdict(str, Enum):
    CORRECT = "CORRECT"
    PARTIALLY_CORRECT = "PARTIALLY_CORRECT"
    EARLY = "EARLY"
    LATE = "LATE"
    POORLY_TIMED = "POORLY_TIMED"
    INVALIDATED = "INVALIDATED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class ContributorCategory(str, Enum):
    STOCK_SELECTION = "STOCK_SELECTION"
    ENTRY_TIMING = "ENTRY_TIMING"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    EXIT_LOGIC = "EXIT_LOGIC"
    MARKET_CONDITIONS = "MARKET_CONDITIONS"
    SECTOR_CONDITIONS = "SECTOR_CONDITIONS"
    VOLATILITY = "VOLATILITY"
    LIQUIDITY = "LIQUIDITY"
    NEWS_OR_EVENT = "NEWS_OR_EVENT"
    PRICE_NOISE = "PRICE_NOISE"
    ADMINISTRATIVE_ACTION = "ADMINISTRATIVE_ACTION"


class SupportLevel(str, Enum):
    STRONGLY_SUPPORTED = "STRONGLY_SUPPORTED"
    SUPPORTED = "SUPPORTED"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    CONFLICTED = "CONFLICTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


@dataclass(frozen=True)
class SignalEvaluation:
    signal_id: str
    signal_name: str
    expected_interpretation: str | None
    entry_evidence_id: str | None
    comparison_evidence_ids: list[str]
    status: str
    evidence_class: str
    confidence_band: str
    explanation_claim_id: str
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContributorAssessment:
    category: str
    support_level: str
    evidence_class: str
    confidence_band: str
    supporting_evidence_ids: list[str]
    opposing_evidence_ids: list[str]
    claim_id: str
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceAttributionResult:
    trade_id: int
    evidence_items: list[EvidenceItem]
    claims: list[PostmortemClaim]
    signal_scorecard: list[SignalEvaluation]
    contributor_assessments: list[ContributorAssessment]
    primary_contributor: str | None
    primary_contributor_claim_id: str | None
    thesis_verdict: str
    thesis_verdict_claim_id: str
    rule_registry_version: str
    calculation_version: str = ATTRIBUTION_RULES_VERSION
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rule registry entries — every rule this module can fire is declared here,
# with its own identity and evidence contract, so a test can enumerate
# RULE_REGISTRY and assert every claim this module produces cites a real,
# registered rule_id/rule_version (see test_evidence_attribution_rules.py).
# ---------------------------------------------------------------------------

_RULE_ENTRY_FACT = register_rule(RuleDefinition(
    rule_id="SIG_ENTRY_FACT_001", rule_version="1.0.0", report_section="signal_scorecard",
    required_evidence=("entry_snapshot_field",), optional_evidence=(),
    supporting_condition="the named entry-snapshot field is non-null",
    opposing_condition="not applicable — this rule only records a fact, never a for/against judgment",
    insufficient_evidence_condition="the named entry-snapshot field is null or no snapshot exists",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))

_RULE_THESIS_CAPTURE = register_rule(RuleDefinition(
    rule_id="THESIS_DIRECTION_PATH_001", rule_version="1.0.0", report_section="thesis_verdict",
    required_evidence=("entry_snapshot.recommendation_signal_or_technical_signal", "holding_period_price_path"),
    optional_evidence=(),
    supporting_condition="a captured entry thesis AND point-in-time price-path evidence consistent with it both exist",
    opposing_condition="a captured entry thesis exists but price-path evidence contradicts it",
    insufficient_evidence_condition="no captured entry thesis exists (UNSUPPORTED), or a thesis exists but "
                                     "no price-path evidence is available yet to check it (NOT_ASSESSABLE)",
    permitted_output_classes=(EvidenceClass.INSUFFICIENT_EVIDENCE.value,),
))

_RULE_POSITION_MANAGEMENT = register_rule(RuleDefinition(
    rule_id="CONTRIBUTOR_POSITION_MANAGEMENT_001", rule_version="1.0.0", report_section="contributor_assessments",
    required_evidence=("exit_mechanism", "outcome", "target_distance_at_exit_pct", "stop_distance_at_exit_pct"),
    optional_evidence=(),
    supporting_condition="exit_mechanism=MANUAL, outcome=LOSS, and the exit price finished closer to the "
                          "target level than the stop level (both stored, no outcome-derived signal involved)",
    opposing_condition="exit_mechanism=MANUAL, outcome=LOSS, and the exit price finished closer to the stop",
    insufficient_evidence_condition="stop_loss or target_price was never set on the trade",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))

_RULE_EXIT_LOGIC = register_rule(RuleDefinition(
    rule_id="EXIT_MECHANISM_001", rule_version="1.0.0", report_section="contributor_assessments",
    required_evidence=("exit_reason",), optional_evidence=(),
    supporting_condition="the stored exit_reason does not map to a recognized exit mechanism",
    opposing_condition="not applicable",
    insufficient_evidence_condition="not applicable — exit_reason is always present or NULL, both are directly observed facts",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value,),
))

_RULE_UNACQUIRED_EVIDENCE = register_rule(RuleDefinition(
    rule_id="CONTRIBUTOR_UNACQUIRED_EVIDENCE_001", rule_version="1.0.0", report_section="contributor_assessments",
    required_evidence=(), optional_evidence=(),
    supporting_condition="never — this rule only fires when no evidence-acquisition path exists yet",
    opposing_condition="never",
    insufficient_evidence_condition="always, for categories requiring market/sector/volatility/liquidity/news/"
                                     "price-path evidence this codebase does not yet acquire",
    permitted_output_classes=(EvidenceClass.INSUFFICIENT_EVIDENCE.value,),
))

_RULE_ENTRY_SIGNAL_DISAGREEMENT = register_rule(RuleDefinition(
    rule_id="CONTRA_ENTRY_SIGNAL_DISAGREEMENT_001", rule_version="1.0.0", report_section="contradictions",
    required_evidence=("technical_signal", "recommendation_signal", "fundamental_score", "sentiment_score"),
    optional_evidence=(),
    supporting_condition="at least two of the four entry-time signals point in opposite directions",
    opposing_condition="all directionally-assessable entry-time signals agree with each other",
    insufficient_evidence_condition="fewer than two entry-time signals have an assessable direction",
    permitted_output_classes=(EvidenceClass.CONFLICTING_EVIDENCE.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))

_RULE_MOMENTUM_VS_RANGE = register_rule(RuleDefinition(
    rule_id="CONTRA_MOMENTUM_VS_RANGE_001", rule_version="1.0.0", report_section="contradictions",
    required_evidence=("technical_signal", "execution_range_position"), optional_evidence=(),
    supporting_condition="technical_signal is bullish AND execution_range_position=ABOVE_RANGE "
                          "(chased price above the recommended entry range)",
    opposing_condition="technical_signal is bullish AND execution_range_position is WITHIN_RANGE or BELOW_RANGE",
    insufficient_evidence_condition="technical_signal has no assessable direction, or execution_range_position is UNKNOWN",
    permitted_output_classes=(EvidenceClass.CONFLICTING_EVIDENCE.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))

_RULE_PRICE_PATH_CONTRADICTIONS = register_rule(RuleDefinition(
    rule_id="CONTRA_REQUIRES_PRICE_PATH_001", rule_version="1.0.0", report_section="contradictions",
    required_evidence=("holding_period_price_path", "benchmark_series"), optional_evidence=(),
    supporting_condition="never in this sprint — no price-path or benchmark evidence is acquired yet",
    opposing_condition="never in this sprint",
    insufficient_evidence_condition="always in this sprint, for any contradiction check that needs "
                                     "favourable-signal-vs-adverse-price-behavior, benchmark-relative movement, "
                                     "or briefly-followed-then-reversed-thesis evidence",
    permitted_output_classes=(EvidenceClass.INSUFFICIENT_EVIDENCE.value,),
))

_RULE_PRIMARY_CONTRIBUTOR_SELECTION = register_rule(RuleDefinition(
    rule_id="PRIMARY_CONTRIBUTOR_SELECTION_001", rule_version="1.0.0", report_section="primary_contributor",
    required_evidence=("contributor_assessments",), optional_evidence=(),
    supporting_condition="exactly one contributor category is STRONGLY_SUPPORTED and no other category is "
                          "SUPPORTED or CONFLICTED",
    opposing_condition="not applicable — this rule only decides whether to select a single winner, never for/against",
    insufficient_evidence_condition="no category reaches STRONGLY_SUPPORTED, or more than one competing "
                                     "category is SUPPORTED/CONFLICTED",
    permitted_output_classes=(EvidenceClass.INSUFFICIENT_EVIDENCE.value,),
))


# ---------------------------------------------------------------------------
# Directional helpers (shared, non-circular — these compare stored values
# to each other, never to the trade's own outcome)
# ---------------------------------------------------------------------------

def _is_finite_number(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _technical_direction(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    v = value.strip().upper()
    if v in ("BUY", "BULLISH", "STRONG_BUY"):
        return 1
    if v in ("SELL", "BEARISH", "STRONG_SELL"):
        return -1
    return None


def _score_direction(score: float | None, *, neutral_midpoint: float) -> int | None:
    if not _is_finite_number(score):
        return None
    if score > neutral_midpoint:
        return 1
    if score < neutral_midpoint:
        return -1
    return None


_SIGNAL_SPECS = (
    ("technical_signal", "Technical signal", _technical_direction),
    ("recommendation_signal", "Recommendation signal", _technical_direction),
    ("fundamental_score", "Fundamental score", lambda v: _score_direction(v, neutral_midpoint=50.0)),
    ("sentiment_score", "Sentiment score", lambda v: _score_direction(v, neutral_midpoint=50.0)),
)


# ---------------------------------------------------------------------------
# Signal scorecard — Stage 6/7: no signal is ever scored against the
# trade's own outcome.
# ---------------------------------------------------------------------------

def _build_signal_scorecard(
    trade_id: int, snapshot: EntrySnapshot | None
) -> tuple[list[EvidenceItem], list[PostmortemClaim], list[SignalEvaluation]]:
    evidence_items: list[EvidenceItem] = []
    claims: list[PostmortemClaim] = []
    scorecard: list[SignalEvaluation] = []

    for field_name, display_name, direction_fn in _SIGNAL_SPECS:
        value = getattr(snapshot, field_name, None) if snapshot is not None else None
        claim_id = make_claim_id(trade_id, f"{_RULE_ENTRY_FACT.rule_id}:{field_name}")

        if value is None:
            claim = insufficient_evidence_claim(
                claim_id=claim_id, report_section="signal_scorecard", factor=field_name,
                rule_id=_RULE_ENTRY_FACT.rule_id, rule_version=_RULE_ENTRY_FACT.rule_version,
                missing_evidence=[f"{field_name} was not captured in this trade's entry snapshot"],
            )
            claims.append(claim)
            scorecard.append(SignalEvaluation(
                signal_id=field_name, signal_name=display_name, expected_interpretation=None,
                entry_evidence_id=None, comparison_evidence_ids=[],
                status=SignalStatus.INSUFFICIENT_EVIDENCE.value,
                evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value,
                confidence_band=ConfidenceBand.NOT_ASSESSABLE.value, explanation_claim_id=claim_id,
                limitations=["no entry-time value was captured for this signal"],
            ))
            continue

        ev_id = make_evidence_id(trade_id, "entry_snapshot", field_name)
        evidence_items.append(EvidenceItem(
            evidence_id=ev_id, category="entry_snapshot", name=field_name, value=value, units=None,
            observation_timestamp=snapshot.recommendation_generated_at if snapshot else None,
            source="paper_trade_entry_snapshot", source_type=SourceType.CLIENT_REPORTED.value,
            verification_level=EvidenceVerificationLevel.CLIENT_REPORTED.value,
            freshness_status=FreshnessStatus.POINT_IN_TIME_VALID.value,
            limitations=["client-reported at Buy time; no server-side corroboration exists for this field"],
        ))

        direction = direction_fn(value)
        expected = "bullish" if direction == 1 else "bearish" if direction == -1 else None

        # Stage 6/7: NEVER compare this to the trade's outcome. There is no
        # later, independent, timestamped value for this field anywhere in
        # this codebase yet — so the only honest status is NOT_TESTABLE:
        # the entry fact is real, its later performance is unknown.
        claim = PostmortemClaim(
            claim_id=claim_id, report_section="signal_scorecard", factor=field_name,
            claim_text=(
                f"At entry, {display_name.lower()} was recorded as {value!r}"
                + (f" ({expected})" if expected else "")
                + ". Its later effectiveness cannot be assessed: this report does not yet capture any "
                "comparable value observed after entry, so this is a recorded fact, not a performance judgment."
            ),
            evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value,
            confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
            supporting_evidence_ids=[ev_id], opposing_evidence_ids=[], missing_evidence=_MISSING_LATER_EVIDENCE,
            contradiction_flags=[], rule_id=_RULE_ENTRY_FACT.rule_id, rule_version=_RULE_ENTRY_FACT.rule_version,
            limitations=["entry-only evidence; no later comparison exists"],
        )
        claims.append(claim)
        scorecard.append(SignalEvaluation(
            signal_id=field_name, signal_name=display_name, expected_interpretation=expected,
            entry_evidence_id=ev_id, comparison_evidence_ids=[],
            status=SignalStatus.NOT_TESTABLE.value, evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value,
            confidence_band=ConfidenceBand.NOT_ASSESSABLE.value, explanation_claim_id=claim_id,
            limitations=["no later comparable value exists to test this signal's later performance against"],
        ))

    return evidence_items, claims, scorecard


# ---------------------------------------------------------------------------
# Thesis verdict — Stage 8: never derived directly from WIN/LOSS.
# ---------------------------------------------------------------------------

def _build_thesis_verdict(trade_id: int, snapshot: EntrySnapshot | None) -> tuple[str, PostmortemClaim]:
    has_captured_thesis = snapshot is not None and (
        snapshot.recommendation_signal is not None or snapshot.technical_signal is not None
    )
    claim_id = make_claim_id(trade_id, _RULE_THESIS_CAPTURE.rule_id)

    if not has_captured_thesis:
        claim = insufficient_evidence_claim(
            claim_id=claim_id, report_section="thesis_verdict", factor="thesis_verdict",
            rule_id=_RULE_THESIS_CAPTURE.rule_id, rule_version=_RULE_THESIS_CAPTURE.rule_version,
            missing_evidence=["no recommendation_signal or technical_signal was captured at entry — "
                               "there is no entry thesis on record to evaluate"],
        )
        # UNSUPPORTED is a distinct semantic from the fallback-worded
        # INSUFFICIENT_EVIDENCE claims elsewhere: an outcome exists but no
        # thesis was ever on record, per Stage 8's own definition. The
        # claim object still uses the exact fallback sentence (required
        # for any INSUFFICIENT_EVIDENCE-class claim); the distinct
        # ThesisVerdict.UNSUPPORTED enum value is what the caller renders.
        return ThesisVerdict.UNSUPPORTED.value, claim

    # A thesis was captured, but this sprint has no point-in-time
    # price-path evidence to check it against (that is a later,
    # separately-scoped sprint) — so NOT_ASSESSABLE, never guessed at
    # CORRECT/EARLY/LATE/INVALIDATED without that evidence.
    claim = insufficient_evidence_claim(
        claim_id=claim_id, report_section="thesis_verdict", factor="thesis_verdict",
        rule_id=_RULE_THESIS_CAPTURE.rule_id, rule_version=_RULE_THESIS_CAPTURE.rule_version,
        missing_evidence=["a captured entry thesis exists, but no point-in-time price-path evidence "
                           "is available yet to check whether it played out"],
    )
    return ThesisVerdict.NOT_ASSESSABLE.value, claim


# ---------------------------------------------------------------------------
# Multi-contributor model — Stage 9: no forced single root cause.
# ---------------------------------------------------------------------------

_UNACQUIRED_CATEGORIES = (
    ContributorCategory.STOCK_SELECTION,
    ContributorCategory.ENTRY_TIMING,
    ContributorCategory.MARKET_CONDITIONS,
    ContributorCategory.SECTOR_CONDITIONS,
    ContributorCategory.VOLATILITY,
    ContributorCategory.LIQUIDITY,
    ContributorCategory.NEWS_OR_EVENT,
    ContributorCategory.PRICE_NOISE,
    ContributorCategory.ADMINISTRATIVE_ACTION,
)


def _build_position_management_contributor(
    trade_id: int, postmortem: DeterministicPostmortem
) -> tuple[ContributorAssessment, PostmortemClaim]:
    claim_id = make_claim_id(trade_id, _RULE_POSITION_MANAGEMENT.rule_id)
    td, sd = postmortem.target_distance_at_exit_pct, postmortem.stop_distance_at_exit_pct

    if td is None or sd is None:
        claim = insufficient_evidence_claim(
            claim_id=claim_id, report_section="contributor_assessments", factor="POSITION_MANAGEMENT",
            rule_id=_RULE_POSITION_MANAGEMENT.rule_id, rule_version=_RULE_POSITION_MANAGEMENT.rule_version,
            missing_evidence=["stop_loss or target_price was not set on this trade"],
        )
        assessment = ContributorAssessment(
            category=ContributorCategory.POSITION_MANAGEMENT.value, support_level=SupportLevel.NOT_ASSESSABLE.value,
            evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
            supporting_evidence_ids=[], opposing_evidence_ids=[], claim_id=claim_id,
        )
        return assessment, claim

    exit_price_ev = make_evidence_id(trade_id, "trade", "exit_price_vs_levels")
    ev_item_note = f"target_distance_at_exit_pct={td}, stop_distance_at_exit_pct={sd}"

    if (
        postmortem.exit_mechanism == ExitMechanism.MANUAL
        and postmortem.outcome == Outcome.LOSS
        and abs(td) < abs(sd)
    ):
        claim = PostmortemClaim(
            claim_id=claim_id, report_section="contributor_assessments", factor="POSITION_MANAGEMENT",
            claim_text=(
                "The trade was closed manually at a loss while the exit price was still closer to the "
                f"target level than the stop level ({ev_item_note}) — consistent with the position being "
                "closed out before the stop was reached, though this does not identify why the manual "
                "decision was made."
            ),
            evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.MODERATE.value,
            supporting_evidence_ids=[exit_price_ev], opposing_evidence_ids=[], missing_evidence=[],
            contradiction_flags=[], rule_id=_RULE_POSITION_MANAGEMENT.rule_id,
            rule_version=_RULE_POSITION_MANAGEMENT.rule_version,
            limitations=["derived from stored exit/target/stop prices only; does not know the trader's actual reasoning"],
        )
        assessment = ContributorAssessment(
            category=ContributorCategory.POSITION_MANAGEMENT.value, support_level=SupportLevel.SUPPORTED.value,
            evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.MODERATE.value,
            supporting_evidence_ids=[exit_price_ev], opposing_evidence_ids=[], claim_id=claim_id,
        )
        return assessment, claim

    claim = insufficient_evidence_claim(
        claim_id=claim_id, report_section="contributor_assessments", factor="POSITION_MANAGEMENT",
        rule_id=_RULE_POSITION_MANAGEMENT.rule_id, rule_version=_RULE_POSITION_MANAGEMENT.rule_version,
        missing_evidence=["the stored exit/target/stop prices do not match this rule's supporting pattern "
                           "(manual exit at a loss, closer to target than stop)"],
    )
    assessment = ContributorAssessment(
        category=ContributorCategory.POSITION_MANAGEMENT.value, support_level=SupportLevel.NOT_ASSESSABLE.value,
        evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
        supporting_evidence_ids=[], opposing_evidence_ids=[], claim_id=claim_id,
    )
    return assessment, claim


def _build_exit_logic_contributor(
    trade_id: int, postmortem: DeterministicPostmortem
) -> tuple[ContributorAssessment, PostmortemClaim]:
    claim_id = make_claim_id(trade_id, _RULE_EXIT_LOGIC.rule_id)
    ev_id = make_evidence_id(trade_id, "trade", "exit_reason")

    if postmortem.exit_mechanism == ExitMechanism.UNKNOWN and postmortem.outcome != Outcome.WIN:
        claim = PostmortemClaim(
            claim_id=claim_id, report_section="contributor_assessments", factor="EXIT_LOGIC",
            claim_text=(
                "The stored exit reason does not map to a recognized exit mechanism (TARGET_HIT/STOP_LOSS/"
                "MANUAL), which limits how confidently this outcome's exit can be attributed."
            ),
            evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.LOW.value,
            supporting_evidence_ids=[ev_id], opposing_evidence_ids=[], missing_evidence=[], contradiction_flags=[],
            rule_id=_RULE_EXIT_LOGIC.rule_id, rule_version=_RULE_EXIT_LOGIC.rule_version,
            limitations=["identifies a data-quality limitation, not a market cause"],
        )
        assessment = ContributorAssessment(
            category=ContributorCategory.EXIT_LOGIC.value, support_level=SupportLevel.WEAKLY_SUPPORTED.value,
            evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.LOW.value,
            supporting_evidence_ids=[ev_id], opposing_evidence_ids=[], claim_id=claim_id,
        )
        return assessment, claim

    claim = insufficient_evidence_claim(
        claim_id=claim_id, report_section="contributor_assessments", factor="EXIT_LOGIC",
        rule_id=_RULE_EXIT_LOGIC.rule_id, rule_version=_RULE_EXIT_LOGIC.rule_version,
        missing_evidence=["exit mechanism is known (or the trade won), so this rule does not apply"],
    )
    assessment = ContributorAssessment(
        category=ContributorCategory.EXIT_LOGIC.value, support_level=SupportLevel.NOT_ASSESSABLE.value,
        evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
        supporting_evidence_ids=[], opposing_evidence_ids=[], claim_id=claim_id,
    )
    return assessment, claim


def _build_unacquired_contributor(
    trade_id: int, category: ContributorCategory
) -> tuple[ContributorAssessment, PostmortemClaim]:
    claim_id = make_claim_id(trade_id, f"{_RULE_UNACQUIRED_EVIDENCE.rule_id}:{category.value}")
    claim = insufficient_evidence_claim(
        claim_id=claim_id, report_section="contributor_assessments", factor=category.value,
        rule_id=_RULE_UNACQUIRED_EVIDENCE.rule_id, rule_version=_RULE_UNACQUIRED_EVIDENCE.rule_version,
        missing_evidence=_MISSING_MARKET_DATA,
    )
    assessment = ContributorAssessment(
        category=category.value, support_level=SupportLevel.NOT_ASSESSABLE.value,
        evidence_class=EvidenceClass.INSUFFICIENT_EVIDENCE.value, confidence_band=ConfidenceBand.NOT_ASSESSABLE.value,
        supporting_evidence_ids=[], opposing_evidence_ids=[], claim_id=claim_id,
        limitations=list(_MISSING_MARKET_DATA),
    )
    return assessment, claim


def _select_primary_contributor(
    contributors: list[ContributorAssessment],
) -> tuple[str | None, str | None]:
    """Populates primary_contributor ONLY when exactly one category is
    STRONGLY_SUPPORTED and no other category is SUPPORTED or CONFLICTED —
    per Stage 9. Given this sprint's rules top out at SUPPORTED (never
    STRONGLY_SUPPORTED), this will typically return (None, None), which is
    the intended, honest default — not a bug to "fix" by lowering the bar."""
    strongly = [c for c in contributors if c.support_level == SupportLevel.STRONGLY_SUPPORTED.value]
    competing = [
        c for c in contributors
        if c.support_level in (SupportLevel.SUPPORTED.value, SupportLevel.CONFLICTED.value)
    ]
    if len(strongly) == 1 and not [c for c in competing if c.category != strongly[0].category]:
        return strongly[0].category, strongly[0].claim_id
    return None, None


# ---------------------------------------------------------------------------
# Contradiction handling — Stage 10.
# ---------------------------------------------------------------------------

def _build_entry_signal_disagreement_claim(trade_id: int, snapshot: EntrySnapshot | None) -> PostmortemClaim:
    claim_id = make_claim_id(trade_id, _RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_id)
    if snapshot is None:
        return insufficient_evidence_claim(
            claim_id=claim_id, report_section="contradictions", factor="entry_signal_agreement",
            rule_id=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_id, rule_version=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_version,
            missing_evidence=["no entry snapshot exists for this trade"],
        )

    directions: dict[str, int] = {}
    evidence_ids: dict[str, str] = {}
    for field_name, _display, direction_fn in _SIGNAL_SPECS:
        value = getattr(snapshot, field_name, None)
        direction = direction_fn(value) if value is not None else None
        if direction is not None:
            directions[field_name] = direction
            evidence_ids[field_name] = make_evidence_id(trade_id, "entry_snapshot", field_name)

    if len(directions) < 2:
        return insufficient_evidence_claim(
            claim_id=claim_id, report_section="contradictions", factor="entry_signal_agreement",
            rule_id=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_id, rule_version=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_version,
            missing_evidence=["fewer than two entry-time signals have an assessable direction"],
        )

    bullish = [f for f, d in directions.items() if d == 1]
    bearish = [f for f, d in directions.items() if d == -1]

    if bullish and bearish:
        return PostmortemClaim(
            claim_id=claim_id, report_section="contradictions", factor="entry_signal_agreement",
            claim_text=(
                f"At entry, {', '.join(bullish)} pointed bullish while {', '.join(bearish)} pointed bearish — "
                "these entry-time signals disagreed with each other."
            ),
            evidence_class=EvidenceClass.CONFLICTING_EVIDENCE.value, confidence_band=ConfidenceBand.MODERATE.value,
            supporting_evidence_ids=[evidence_ids[f] for f in bullish],
            opposing_evidence_ids=[evidence_ids[f] for f in bearish],
            missing_evidence=[], contradiction_flags=["ENTRY_SIGNAL_DISAGREEMENT"],
            rule_id=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_id, rule_version=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_version,
        )

    agreeing = bullish or bearish
    return PostmortemClaim(
        claim_id=claim_id, report_section="contradictions", factor="entry_signal_agreement",
        claim_text=f"At entry, all assessable signals ({', '.join(agreeing)}) agreed with each other.",
        evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.MODERATE.value,
        supporting_evidence_ids=[evidence_ids[f] for f in agreeing], opposing_evidence_ids=[], missing_evidence=[],
        contradiction_flags=[], rule_id=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_id,
        rule_version=_RULE_ENTRY_SIGNAL_DISAGREEMENT.rule_version,
    )


def _build_momentum_vs_range_claim(trade_id: int, snapshot: EntrySnapshot | None) -> PostmortemClaim:
    claim_id = make_claim_id(trade_id, _RULE_MOMENTUM_VS_RANGE.rule_id)
    if snapshot is None:
        return insufficient_evidence_claim(
            claim_id=claim_id, report_section="contradictions", factor="momentum_vs_entry_range",
            rule_id=_RULE_MOMENTUM_VS_RANGE.rule_id, rule_version=_RULE_MOMENTUM_VS_RANGE.rule_version,
            missing_evidence=["no entry snapshot exists for this trade"],
        )

    tech_direction = _technical_direction(snapshot.technical_signal)
    range_position = snapshot.execution_range_position

    if tech_direction is None or range_position in (None, "UNKNOWN"):
        return insufficient_evidence_claim(
            claim_id=claim_id, report_section="contradictions", factor="momentum_vs_entry_range",
            rule_id=_RULE_MOMENTUM_VS_RANGE.rule_id, rule_version=_RULE_MOMENTUM_VS_RANGE.rule_version,
            missing_evidence=["technical_signal has no assessable direction, or execution_range_position is UNKNOWN"],
        )

    tech_ev = make_evidence_id(trade_id, "entry_snapshot", "technical_signal")
    range_ev = make_evidence_id(trade_id, "entry_snapshot", "execution_range_position")

    if tech_direction == 1 and range_position == "ABOVE_RANGE":
        return PostmortemClaim(
            claim_id=claim_id, report_section="contradictions", factor="momentum_vs_entry_range",
            claim_text=(
                "The technical signal was bullish, but the execution price was already above the "
                "recommended entry range — consistent with entering after the anticipated move had "
                "already partly occurred."
            ),
            evidence_class=EvidenceClass.CONFLICTING_EVIDENCE.value, confidence_band=ConfidenceBand.LOW.value,
            supporting_evidence_ids=[tech_ev], opposing_evidence_ids=[range_ev], missing_evidence=[],
            contradiction_flags=["MOMENTUM_VS_RANGE"], rule_id=_RULE_MOMENTUM_VS_RANGE.rule_id,
            rule_version=_RULE_MOMENTUM_VS_RANGE.rule_version,
        )

    return PostmortemClaim(
        claim_id=claim_id, report_section="contradictions", factor="momentum_vs_entry_range",
        claim_text="The technical signal and the execution-range position at entry do not conflict.",
        evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.MODERATE.value,
        supporting_evidence_ids=[tech_ev, range_ev], opposing_evidence_ids=[], missing_evidence=[],
        contradiction_flags=[], rule_id=_RULE_MOMENTUM_VS_RANGE.rule_id,
        rule_version=_RULE_MOMENTUM_VS_RANGE.rule_version,
    )


def _build_price_path_unavailable_claims(trade_id: int) -> list[PostmortemClaim]:
    """The four contradiction patterns the Sprint 1 brief names that
    genuinely require price-path/benchmark evidence this codebase does
    not yet acquire — each honestly resolves to INSUFFICIENT_EVIDENCE
    rather than being silently skipped, so a caller can see they were
    considered and correctly deferred, not forgotten."""
    factors = [
        "favourable_entry_signal_vs_adverse_price_behavior",
        "favourable_stock_movement_vs_adverse_benchmark_movement",
        "positive_pnl_vs_unsupported_thesis",
        "negative_pnl_vs_briefly_followed_thesis",
    ]
    return [
        insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, f"{_RULE_PRICE_PATH_CONTRADICTIONS.rule_id}:{factor}"),
            report_section="contradictions", factor=factor,
            rule_id=_RULE_PRICE_PATH_CONTRADICTIONS.rule_id, rule_version=_RULE_PRICE_PATH_CONTRADICTIONS.rule_version,
            missing_evidence=["holding-period price-path and/or benchmark evidence is not acquired by this "
                               "codebase yet (a later, separately-scoped sprint)"],
        )
        for factor in factors
    ]


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def build_evidence_attribution(
    postmortem: DeterministicPostmortem, snapshot: EntrySnapshot | None
) -> EvidenceAttributionResult:
    """Pure function: one already-computed `DeterministicPostmortem` plus
    its (possibly `None`) `EntrySnapshot` in, one `EvidenceAttributionResult`
    out. Never recomputes outcome/P&L/duration/exit-mechanism — those are
    read verbatim from `postmortem`."""
    warnings: list[str] = list(postmortem.warnings)
    evidence_items: list[EvidenceItem] = []
    claims: list[PostmortemClaim] = []

    sc_evidence, sc_claims, scorecard = _build_signal_scorecard(postmortem.trade_id, snapshot)
    evidence_items.extend(sc_evidence)
    claims.extend(sc_claims)

    thesis_verdict, thesis_claim = _build_thesis_verdict(postmortem.trade_id, snapshot)
    claims.append(thesis_claim)

    contributors: list[ContributorAssessment] = []
    pm_assessment, pm_claim = _build_position_management_contributor(postmortem.trade_id, postmortem)
    contributors.append(pm_assessment)
    claims.append(pm_claim)
    exit_logic_assessment, exit_logic_claim = _build_exit_logic_contributor(postmortem.trade_id, postmortem)
    contributors.append(exit_logic_assessment)
    claims.append(exit_logic_claim)
    for category in _UNACQUIRED_CATEGORIES:
        unacquired_assessment, unacquired_claim = _build_unacquired_contributor(postmortem.trade_id, category)
        contributors.append(unacquired_assessment)
        claims.append(unacquired_claim)

    primary_contributor, primary_claim_id = _select_primary_contributor(contributors)

    claims.append(_build_entry_signal_disagreement_claim(postmortem.trade_id, snapshot))
    claims.append(_build_momentum_vs_range_claim(postmortem.trade_id, snapshot))
    claims.extend(_build_price_path_unavailable_claims(postmortem.trade_id))

    if primary_contributor is None:
        primary_contributor_claim = insufficient_evidence_claim(
            claim_id=make_claim_id(postmortem.trade_id, _RULE_PRIMARY_CONTRIBUTOR_SELECTION.rule_id),
            report_section="primary_contributor", factor="primary_contributor",
            rule_id=_RULE_PRIMARY_CONTRIBUTOR_SELECTION.rule_id,
            rule_version=_RULE_PRIMARY_CONTRIBUTOR_SELECTION.rule_version,
            missing_evidence=["no single contributor category is strongly supported without a competing, "
                               "equally-or-more supported category"],
        )
        primary_claim_id = primary_contributor_claim.claim_id
        claims.append(primary_contributor_claim)

    return EvidenceAttributionResult(
        trade_id=postmortem.trade_id,
        evidence_items=evidence_items,
        claims=claims,
        signal_scorecard=scorecard,
        contributor_assessments=contributors,
        primary_contributor=primary_contributor,
        primary_contributor_claim_id=primary_claim_id,
        thesis_verdict=thesis_verdict,
        thesis_verdict_claim_id=thesis_claim.claim_id,
        rule_registry_version=ATTRIBUTION_RULES_VERSION,
        calculation_version=ATTRIBUTION_RULES_VERSION,
        warnings=warnings,
    )
