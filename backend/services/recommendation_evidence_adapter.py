"""
Recommendation Consolidation Intelligence — Evidence Normalization Layer
(Epic 005, Sprint #003).

Maps each of the four existing engines' raw EngineResponse dicts into the
common EngineEvidence contract (recommendation_consolidation_contract.py).
Per Sprint #002's Evidence Checkpoint and this sprint's own "Option B"
selection (the lowest-risk implementation boundary): this module reads
existing engine output ONLY — it never modifies business_quality_engine.py,
financial_strength_engine.py, growth_intelligence_engine.py, or
valuation_intelligence_engine.py. Business Quality's missing
`engine_version`/`market` fields (Sprint #002's Discrepancy 1) are supplied
here, by the adapter, as a contract-side default — never by changing
Business Quality's own source.
"""

import uuid

from services.recommendation_consolidation_contract import (
    EngineEvidence, EvidenceStatus, HardGateType,
    RecommendationEvidenceSnapshot, CONTRACT_VERSION, utc_now_iso,
)

# Business Quality has shipped as "v1" since SSDS-003's own implementation
# and has never been re-versioned — confirmed by inspecting its own module
# history; this is an adapter-supplied contract default, not a guess, and
# is named explicitly as such per Sprint #002's "do not silently assume the
# desired data already exists" rule. If business_quality_engine.py is ever
# given its own engine_version field, this default becomes a safe no-op
# (the real value would simply override it below).
_BUSINESS_QUALITY_DEFAULT_ENGINE_VERSION = "v1"

# Grade -> status mapping used when no rejection_reason narrows it further.
_GRADE_TO_STATUS = {
    "strong_buy": EvidenceStatus.SUPPORTED,
    "buy": EvidenceStatus.SUPPORTED,
    "hold": EvidenceStatus.MIXED,
    "watch": EvidenceStatus.WARNING,
    "sell": EvidenceStatus.WARNING,
    "avoid": EvidenceStatus.AVOID,
}

# rejection_reason -> (status, hard_gate) mapping, confirmed against the
# real reason strings each engine's own code emits (grepped directly, not
# assumed) — see Sprint #002's Evidence Contract §6 Hard-Gate table.
_REJECTION_REASON_MAP = {
    "insufficient_data": (EvidenceStatus.UNAVAILABLE, HardGateType.NONE),
    "sector_not_yet_supported": (EvidenceStatus.NOT_APPLICABLE, HardGateType.NONE),
    "liquidity_distress": (EvidenceStatus.AVOID, HardGateType.TRUE_VETO),
    # Business Quality's own fraud-risk path: computed and DESCRIBABLE per
    # Sprint #002's Evidence Contract §6 ("RCI may treat it as a veto in
    # its own narrative"), but per THIS sprint's explicit rule ("do not
    # add it as a new veto... do not change any production behavior"),
    # this adapter tags it descriptively (true_veto) without enforcing
    # anything -- no code path anywhere in this package excludes or
    # filters based on this tag; it is observed, not acted on.
    "fraud_risk": (EvidenceStatus.AVOID, HardGateType.TRUE_VETO),
    "distress_and_aggressive_accruals": (EvidenceStatus.AVOID, HardGateType.TRUE_VETO),
}


def _val(metadata: dict, key: str):
    return metadata.get(key) if metadata else None


def _grade_value(grade) -> str | None:
    """Engine grades arrive either as the raw string (from .to_dict()) or
    a Grade enum instance — normalized here, read-only, never mutating
    the source object."""
    if grade is None:
        return None
    return grade.value if hasattr(grade, "value") else str(grade)


def _normalize(
    engine_name: str,
    engine_response: dict | None,
    *,
    market: str,
    default_engine_version: str | None = None,
    applicable: bool = True,
    feature_disabled: bool = False,
) -> EngineEvidence:
    """Shared normalization core every per-engine adapter below calls.
    Never raises -- an unexpected shape degrades to UNAVAILABLE, mirroring
    every existing engine closure's own BaseException-guarded philosophy."""
    if not applicable:
        return EngineEvidence(
            engine_name=engine_name, engine_version=default_engine_version, market=market,
            sector_bucket=None, score=None, grade=None, confidence=None,
            status=EvidenceStatus.NOT_APPLICABLE, hard_gate=HardGateType.NONE,
            positive_evidence=(), negative_evidence=(), warnings=(),
            reason_code="not_applicable_for_market", data_completeness_pct=None,
        )

    if not engine_response:
        return EngineEvidence(
            engine_name=engine_name, engine_version=default_engine_version, market=market,
            sector_bucket=None, score=None, grade=None, confidence=None,
            status=EvidenceStatus.UNAVAILABLE, hard_gate=HardGateType.NONE,
            positive_evidence=(), negative_evidence=(), warnings=(),
            reason_code="no_result", data_completeness_pct=None,
        )

    try:
        grade = _grade_value(engine_response.get("grade"))
        metadata = engine_response.get("metadata") or {}
        rejection_reason = _val(metadata, "rejection_reason")

        if grade == "rejected" and rejection_reason in _REJECTION_REASON_MAP:
            status, hard_gate = _REJECTION_REASON_MAP[rejection_reason]
        elif grade == "rejected":
            # An engine-specific rejection reason this adapter doesn't yet
            # recognize -- degrade to UNAVAILABLE (never fabricate a guess
            # at severity), per this sprint's "never silently convert to
            # negative evidence" rule.
            status, hard_gate = EvidenceStatus.UNAVAILABLE, HardGateType.NONE
        else:
            status = _GRADE_TO_STATUS.get(grade, EvidenceStatus.UNAVAILABLE)
            hard_gate = HardGateType.NONE

        if feature_disabled:
            # The engine computed a real result, but a kill switch
            # currently prevents it from influencing live confidence
            # (Valuation Intelligence, both markets, today). Per Sprint
            # #002's own taxonomy: the score may still be cited for
            # context, but status must say so explicitly.
            status = EvidenceStatus.FEATURE_DISABLED

        engine_version = _val(metadata, "engine_version") or default_engine_version

        return EngineEvidence(
            engine_name=engine_name,
            engine_version=engine_version,
            market=_val(metadata, "market") or market,
            sector_bucket=_val(metadata, "sector_bucket"),
            score=engine_response.get("score"),
            grade=grade,
            confidence=engine_response.get("confidence"),
            status=status,
            hard_gate=hard_gate,
            positive_evidence=tuple(engine_response.get("strengths") or ()),
            negative_evidence=tuple(engine_response.get("weaknesses") or ()),
            warnings=tuple(engine_response.get("risks") or ()),
            reason_code=rejection_reason,
            data_completeness_pct=_val(metadata, "data_completeness_pct"),
        )
    except BaseException:
        return EngineEvidence(
            engine_name=engine_name, engine_version=default_engine_version, market=market,
            sector_bucket=None, score=None, grade=None, confidence=None,
            status=EvidenceStatus.EXECUTION_ERROR, hard_gate=HardGateType.NONE,
            positive_evidence=(), negative_evidence=(), warnings=(),
            reason_code="adapter_error", data_completeness_pct=None,
        )


def adapt_business_quality(engine_response: dict | None, *, market: str) -> EngineEvidence:
    """Source fields consumed: score, grade, confidence, strengths,
    weaknesses, risks, metadata.{sector, sector_bucket, data_completeness_pct,
    rejection_reason}. Fields unavailable: metadata.engine_version,
    metadata.market (Sprint #002's Discrepancy 1) -- supplied here as an
    adapter-side default (_BUSINESS_QUALITY_DEFAULT_ENGINE_VERSION), per
    this sprint's "safest backward-compatible manner" instruction; the
    `market` parameter this function itself receives is used directly
    rather than inferred from the engine's own (absent) metadata field.
    Fields intentionally not inferred: applicability (Business Quality has
    no per-company applicability gate the way Valuation Intelligence's
    sector-population-gating does -- it runs for every company in both
    markets). Grade mapping: standard (see _GRADE_TO_STATUS); its own
    fraud-risk REJECTED path maps through _REJECTION_REASON_MAP like every
    other engine's rejection -- no special-casing beyond that shared table.
    """
    return _normalize(
        "business_quality", engine_response, market=market,
        default_engine_version=_BUSINESS_QUALITY_DEFAULT_ENGINE_VERSION,
    )


def adapt_financial_strength(engine_response: dict | None, *, market: str) -> EngineEvidence:
    """Source fields consumed: identical shape to Business Quality, plus
    metadata.engine_version/market (both present, confirmed by direct
    inspection -- no adapter default needed). Fields unavailable: none
    beyond the shared taxonomy gaps. Fields intentionally not inferred:
    none. Applicability: Financial Strength has NO India coverage at all
    (confirmed unchanged since Epic 002) -- this adapter receives
    `engine_response=None` for India (the existing closure already returns
    None for that market) and correctly maps that to NOT_APPLICABLE, not
    UNAVAILABLE, via the `applicable` parameter, since absence-by-market-
    design is a structural fact, not a missing-data accident."""
    return _normalize(
        "financial_strength", engine_response, market=market,
        applicable=(market == "US"),
    )


def adapt_growth_intelligence(engine_response: dict | None, *, market: str) -> EngineEvidence:
    """Source fields consumed: identical shared shape. Fields unavailable:
    none. Grade mapping: standard. Applicability: computed for BOTH
    markets (confirmed unchanged since Epic 003 Sprint #007 -- US receives
    explainability-only output, never None), so `applicable` is always
    True here; whether the score is currently INFLUENCING live confidence
    (India only, by Sprint #006's decision) is a separate fact this
    adapter does not need to represent -- RCI consumes the engine's own
    grade/score for narrative purposes regardless of whether Prediction
    Engine's confidence pipeline currently acts on it."""
    return _normalize("growth_intelligence", engine_response, market=market)


def adapt_valuation_intelligence(
    engine_response: dict | None, *, market: str, confidence_enabled: bool,
) -> EngineEvidence:
    """Source fields consumed: identical shared shape. Fields unavailable:
    none. Applicability: computed for BOTH markets unconditionally
    (confirmed unchanged since Epic 004 Sprint #007). Feature-flag state:
    `confidence_enabled` is supplied by the CALLER (read from the existing,
    unmodified `_valuation_intelligence_confidence_enabled()` kill-switch
    function in prediction_engine.py -- this module never re-implements or
    duplicates that check) -- per this sprint's explicit "must not activate
    Valuation Intelligence kill switches" rule, this adapter only OBSERVES
    the switch's current state, it never flips it. When disabled (the
    default in both markets today), this adapter tags the evidence
    FEATURE_DISABLED regardless of how cheap/expensive the engine's own
    score says the stock is -- the score is still captured for narrative
    context, per Sprint #002's own taxonomy."""
    return _normalize(
        "valuation_intelligence", engine_response, market=market,
        feature_disabled=not confidence_enabled,
    )


def build_recommendation_evidence_snapshot(
    symbol: str,
    market: str,
    *,
    business_quality: dict | None,
    financial_strength: dict | None,
    growth_intelligence: dict | None,
    valuation_intelligence: dict | None,
    valuation_confidence_enabled: bool,
    sector_bucket: str | None = None,
) -> RecommendationEvidenceSnapshot:
    """Composes the four per-engine adapters into one immutable snapshot.

    Reads only already-computed engine outputs (the same dicts
    PredictionEngine.predict() already returns) -- triggers no new
    provider call, no Prediction Engine recomputation, and mutates none
    of the input dicts (every field is copied, never referenced
    in-place, confirmed by each adapter constructing a frozen dataclass).
    """
    engine_evidence = (
        adapt_business_quality(business_quality, market=market),
        adapt_financial_strength(financial_strength, market=market),
        adapt_growth_intelligence(growth_intelligence, market=market),
        adapt_valuation_intelligence(
            valuation_intelligence, market=market, confidence_enabled=valuation_confidence_enabled,
        ),
    )
    return RecommendationEvidenceSnapshot(
        contract_version=CONTRACT_VERSION,
        snapshot_id=str(uuid.uuid4()),
        analysis_timestamp=utc_now_iso(),
        market=market,
        symbol=symbol,
        sector_bucket=sector_bucket,
        engine_evidence=engine_evidence,
        is_snapshot=False,
    )
