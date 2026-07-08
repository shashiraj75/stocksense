"""
AI Equity Research Analyst — Phase 1: LiveIntelligenceSnapshot builder.

Builds a LiveIntelligenceSnapshot from an ALREADY-COMPUTED
``predict()``-shaped dict. This module never calls PredictionEngine,
never reads the shared prediction cache, and never triggers any
computation to obtain its input — the caller owns that (Phase 1 spec
§11 rule 1). It has no production call site in Phase 1: nothing under
backend/services or backend/api imports this package (enforced by the
static non-interference regression test).

Input safety (spec §11 rule 2): the input dict is deep-copied exactly
once up front; every retained value comes from that copy, so the builder
performs zero assignments into the caller's objects and the finished
snapshot shares zero object identity with the input — mutation of either
side after build cannot affect the other. Phase 1 deliberately pays the
deep-copy cost the RCI composer avoided, because a snapshot's whole
purpose is immutability independent of the shared cache, and Phase 1 has
no latency budget to protect (no call site).

Reading discipline (spec §11 rule 5): extraction follows the fixed
mapping tables below — the builder must not opportunistically read keys
outside them. In particular, legacy Daily-Picks-style ``growth_score``/
``valuation_score`` numbers are structurally unreadable here and can
never become modern Growth/Valuation Intelligence evidence (Contract
§5.2; spec §11 rule 6).

No clock is called for content fields: ``generated_at``/``data_as_of``
come from the input dict and stay None when absent — that is the
validator's V-1 rejection, never a now() substitution (spec §11 rule 8).
"""

from __future__ import annotations

import copy
from dataclasses import replace
from numbers import Number
from typing import Any

from services.research_analyst.intelligence_snapshot import (
    BUILDER_VERSION,
    EvidenceItem,
    EvidenceState,
    EvidenceTier,
    HORIZONS,
    LIVE_SNAPSHOT_TYPE,
    LiveIntelligenceSnapshot,
    MARKET_CURRENCY,
    MARKET_EXCHANGE,
    MARKETS,
    SNAPSHOT_CONTRACT_VERSION,
    SnapshotAvailability,
    SnapshotScope,
    SnapshotTimestamps,
    USER_DECISION_BOUNDARY,
    compute_snapshot_hash,
    deep_freeze,
)


class UnsupportedScopeError(ValueError):
    """Raised for a market or horizon outside the Phase 1 vocabulary.
    The Contract's reserved "investment" horizon is rejected here rather
    than silently mapped (spec §8) — widening is a contract change."""


class ScopeMismatchError(ValueError):
    """Raised when the input dict's own symbol/market/horizon fields
    disagree with the caller-supplied scope. Neither side is silently
    trusted (Contract §6.5 mixing rule, applied as early as possible)."""


# ---------------------------------------------------------------------------
# Fixed mapping tables (spec §8/§9). The builder reads NOTHING else.
# ---------------------------------------------------------------------------

# predict() keys copied verbatim (deep-copied) into decision_context.
_DECISION_CONTEXT_KEYS = (
    "signal", "confidence", "composite_score", "score_band", "target_price",
    "trade_levels", "price_reference", "confidence_breakdown", "factor_contributions",
)

# owner -> (input key, domain) for the four intelligence engines plus the
# legacy-parallel quality factor. Grade/score extraction is uniform.
_ENGINE_OWNERS = (
    ("BusinessQuality", "business_quality", "quality"),
    ("FinancialStrength", "financial_strength", "financial_strength"),
    ("GrowthIntelligence", "growth_intelligence", "growth"),
    ("ValuationIntelligence", "valuation_intelligence", "valuation"),
    ("QualityFactorsLegacy", "quality_factors", "quality"),
)

_MARKET_CONTEXT_KEYS = ("market_regime", "global_context")


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, bool)) or (isinstance(value, Number) and not isinstance(value, bool)) or isinstance(value, bool)


def _status_item(owner: str, domain: str, state: EvidenceState, reason: str,
                 *, market: str, captured_at: str | None, provenance_ref: str) -> EvidenceItem:
    """The single limitation item every non-SUPPORTED owner carries, so a
    consumer can always see WHY a domain is absent (Contract §8.2)."""
    return EvidenceItem(
        evidence_id=f"{owner}:{domain}:status",
        owner=owner,
        domain=domain,
        status=state,
        tier=EvidenceTier.UNKNOWN,
        claim_type="limitation",
        label=f"{owner} evidence is {state.value.lower().replace('_', ' ')}",
        value=None,
        display_value=None,
        source_reference=None,
        as_of=None,
        captured_at=captured_at,
        market=market,
        horizon="not_applicable",
        provenance_ref=provenance_ref,
        reason=reason,
    )


def _fact(owner: str, domain: str, slug: str, label: str, value: Any,
          *, market: str, horizon: str, captured_at: str | None,
          as_of: str | None, provenance_ref: str, claim_type: str = "fact") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"{owner}:{domain}:{slug}",
        owner=owner,
        domain=domain,
        status=EvidenceState.SUPPORTED,
        tier=EvidenceTier.CONFIRMED,
        claim_type=claim_type,
        label=label,
        value=value,
        display_value=str(value),
        source_reference=None,
        as_of=as_of,
        captured_at=captured_at,
        market=market,
        horizon=horizon,
        provenance_ref=provenance_ref,
        reason=None,
    )


def _classify_engine_payload(owner: str, payload: Any, present: bool, market: str) -> tuple[EvidenceState, str]:
    """Map one engine's raw field to (state, reason) per spec §10. Only
    non-SUPPORTED outcomes return a reason; SUPPORTED returns ("", "")-like
    sentinel handled by the caller."""
    if not present:
        return EvidenceState.MISSING, "expected field absent from source result"
    if payload is None:
        if owner == "FinancialStrength" and market == "IN":
            # Market-structural, never company-specific — the exact
            # NOT_APPLICABLE-vs-UNAVAILABLE distinction RCI Sprint #005
            # proved matters (Contract invariant 9).
            return (EvidenceState.NOT_APPLICABLE,
                    "Financial Strength Intelligence has no India implementation; "
                    "market-structural, not a company-specific gap")
        return EvidenceState.UNAVAILABLE, "owner returned no output for this scope"
    if isinstance(payload, dict):
        if payload.get("feature_disabled") is True or payload.get("status") == "feature_disabled":
            return EvidenceState.FEATURE_DISABLED, "feature disabled by configuration"
        if "error" in payload:
            # Bounded by design: the raw owner error text is never copied
            # into evidence (Contract §8.2 EXECUTION_ERROR row).
            return EvidenceState.EXECUTION_ERROR, "owner reported an execution error"
        return EvidenceState.SUPPORTED, ""
    return EvidenceState.UNAVAILABLE, "owner output had an unrecognized shape"


def build_live_intelligence_snapshot(prediction_result: dict, *, symbol: str,
                                     market: str, horizon: str) -> LiveIntelligenceSnapshot:
    """Build an immutable LiveIntelligenceSnapshot from one already-
    computed ``predict()``-shaped dict. Raises UnsupportedScopeError /
    ScopeMismatchError / TypeError on bad scope or input; per-owner
    extraction failures degrade to that owner's EXECUTION_ERROR evidence
    instead of failing the whole build (spec §11 rule 7)."""
    if not isinstance(prediction_result, dict):
        raise TypeError("prediction_result must be a dict shaped like PredictionEngine.predict() output")
    if not symbol or not isinstance(symbol, str):
        raise UnsupportedScopeError("symbol must be a non-empty string")
    if market not in MARKETS:
        raise UnsupportedScopeError(f"market must be one of {MARKETS}, got {market!r}")
    if horizon not in HORIZONS:
        raise UnsupportedScopeError(
            f"horizon must be one of {HORIZONS}, got {horizon!r} — reserved horizons are rejected, never mapped")

    for field_name, supplied in (("symbol", symbol), ("market", market), ("horizon", horizon)):
        in_result = prediction_result.get(field_name)
        if in_result is not None and in_result != supplied:
            raise ScopeMismatchError(
                f"caller-supplied {field_name}={supplied!r} disagrees with the "
                f"source result's own {field_name}={in_result!r}")

    # The one deep copy — everything below reads from src, never from the
    # caller's dict, so input mutation is impossible by construction.
    src = copy.deepcopy(prediction_result)

    generated_at = src.get("generated_at")
    data_as_of = src.get("data_timestamp")
    captured_at = generated_at

    owner_statuses: dict[str, str] = {}
    items: list[EvidenceItem] = []

    # --- PredictionEngine (decision context) -------------------------------
    decision_context = {k: src[k] for k in _DECISION_CONTEXT_KEYS if k in src}
    decision_failed = "error" in src or "signal" not in src
    if decision_failed:
        owner_statuses["PredictionEngine"] = EvidenceState.UNAVAILABLE.value
        items.append(_status_item(
            "PredictionEngine", "decision", EvidenceState.UNAVAILABLE,
            "source result carried no decision output for this scope",
            market=market, captured_at=captured_at, provenance_ref="signal"))
    else:
        owner_statuses["PredictionEngine"] = EvidenceState.SUPPORTED.value
        for slug, key, label in (
            ("signal", "signal", "Current signal"),
            ("confidence", "confidence", "Confidence"),
            ("composite-score", "composite_score", "Composite score"),
            ("score-band", "score_band", "Score band"),
            ("target-price", "target_price", "Target price"),
        ):
            value = src.get(key)
            if _is_scalar(value):
                items.append(_fact("PredictionEngine", "decision", slug, label, value,
                                   market=market, horizon=horizon, captured_at=captured_at,
                                   as_of=data_as_of, provenance_ref=key))
        for i, entry in enumerate(src.get("bull_case") or []):
            if isinstance(entry, str):
                items.append(_fact("PredictionEngine", "decision", f"bull:{i}", "Bull case point",
                                   entry, market=market, horizon=horizon, captured_at=captured_at,
                                   as_of=data_as_of, provenance_ref=f"bull_case[{i}]",
                                   claim_type="owner_conclusion"))
        for i, entry in enumerate(src.get("bear_case") or []):
            if isinstance(entry, str):
                items.append(_fact("PredictionEngine", "risk", f"bear:{i}", "Bear case point",
                                   entry, market=market, horizon=horizon, captured_at=captured_at,
                                   as_of=data_as_of, provenance_ref=f"bear_case[{i}]",
                                   claim_type="counter_evidence"))

    # --- The four intelligence engines + legacy quality factor -------------
    engine_outputs: dict[str, Any] = {}
    for owner, key, domain in _ENGINE_OWNERS:
        try:
            present = key in src
            payload = src.get(key)
            engine_outputs[key] = payload if present else None
            state, reason = _classify_engine_payload(owner, payload, present, market)
            owner_statuses[owner] = state.value
            if state is not EvidenceState.SUPPORTED:
                items.append(_status_item(owner, domain, state, reason, market=market,
                                          captured_at=captured_at, provenance_ref=key))
                continue
            emitted = False
            score = payload.get("score")
            if _is_scalar(score):
                label = ("Composite quality factor score (legacy parallel factor — "
                         "not Business Quality Engine output)"
                         if owner == "QualityFactorsLegacy" else f"{owner} score")
                items.append(_fact(owner, domain, "score", label, score, market=market,
                                   horizon=horizon, captured_at=captured_at, as_of=None,
                                   provenance_ref=f"{key}.score"))
                emitted = True
            grade = payload.get("grade")
            if _is_scalar(grade):
                items.append(_fact(owner, domain, "grade", f"{owner} grade", grade,
                                   market=market, horizon=horizon, captured_at=captured_at,
                                   as_of=None, provenance_ref=f"{key}.grade",
                                   claim_type="owner_conclusion"))
                emitted = True
            if not emitted:
                owner_statuses[owner] = EvidenceState.UNAVAILABLE.value
                items.append(_status_item(owner, domain, EvidenceState.UNAVAILABLE,
                                          "owner output present but carried no recognized fields",
                                          market=market, captured_at=captured_at, provenance_ref=key))
        except Exception:
            # Per-owner isolation: a malformed owner payload degrades that
            # owner only, with a bounded reason — never the whole build.
            owner_statuses[owner] = EvidenceState.EXECUTION_ERROR.value
            items.append(_status_item(owner, domain, EvidenceState.EXECUTION_ERROR,
                                      "owner evidence extraction failed",
                                      market=market, captured_at=captured_at, provenance_ref=key))

    # --- Market context (regime + global) ----------------------------------
    market_context = {k: src.get(k) for k in _MARKET_CONTEXT_KEYS}
    for owner, key, value_key, label in (
        ("MarketRegime", "market_regime", "trend", "Market regime trend"),
        ("GlobalContext", "global_context", "score", "Global market context score"),
    ):
        present = key in src
        payload = src.get(key)
        if present and isinstance(payload, dict) and _is_scalar(payload.get(value_key)):
            owner_statuses[owner] = EvidenceState.SUPPORTED.value
            items.append(_fact(owner, "macro", value_key, label, payload[value_key],
                               market=market, horizon="not_applicable", captured_at=captured_at,
                               as_of=None, provenance_ref=f"{key}.{value_key}"))
        elif not present:
            owner_statuses[owner] = EvidenceState.MISSING.value
            items.append(_status_item(owner, "macro", EvidenceState.MISSING,
                                      "expected field absent from source result",
                                      market=market, captured_at=captured_at, provenance_ref=key))
        else:
            owner_statuses[owner] = EvidenceState.UNAVAILABLE.value
            items.append(_status_item(owner, "macro", EvidenceState.UNAVAILABLE,
                                      "owner returned no output for this scope",
                                      market=market, captured_at=captured_at, provenance_ref=key))

    # --- SnapshotBuilder scope/freshness item -------------------------------
    if data_as_of:
        owner_statuses["SnapshotBuilder"] = EvidenceState.SUPPORTED.value
        items.append(_fact("SnapshotBuilder", "scope", "data-as-of", "Price data as of",
                           data_as_of, market=market, horizon="not_applicable",
                           captured_at=captured_at, as_of=data_as_of,
                           provenance_ref="data_timestamp"))
    else:
        owner_statuses["SnapshotBuilder"] = EvidenceState.MISSING.value
        items.append(_status_item("SnapshotBuilder", "scope", EvidenceState.MISSING,
                                  "source result carried no data timestamp",
                                  market=market, captured_at=captured_at,
                                  provenance_ref="data_timestamp"))

    # --- Availability rollup (plain statuses, never a score — §8.1) ---------
    if owner_statuses["PredictionEngine"] != EvidenceState.SUPPORTED.value:
        overall = "UNAVAILABLE"
    elif all(s == EvidenceState.SUPPORTED.value for s in owner_statuses.values()):
        overall = "COMPLETE"
    else:
        overall = "PARTIAL"

    scope = SnapshotScope(
        symbol=symbol,
        instrument_id=f"{symbol}:{market}",
        market=market,
        exchange=MARKET_EXCHANGE[market],
        currency=MARKET_CURRENCY[market],
        horizon=horizon,
    )
    provisional = LiveIntelligenceSnapshot(
        contract_version=SNAPSHOT_CONTRACT_VERSION,
        snapshot_id=f"live:{symbol}:{market}:{horizon}:{generated_at}",
        snapshot_hash="",
        snapshot_type=LIVE_SNAPSHOT_TYPE,
        scope=scope,
        timestamps=SnapshotTimestamps(generated_at=generated_at, data_as_of=data_as_of),
        availability=SnapshotAvailability(overall_status=overall,
                                          owner_statuses=deep_freeze(owner_statuses)),
        decision_context=deep_freeze(decision_context),
        engine_outputs=deep_freeze(engine_outputs),
        market_context=deep_freeze(market_context),
        evidence_items=tuple(items),
        provenance=deep_freeze({
            "source": "prediction_engine.predict",
            "source_generated_at": generated_at,
            "builder_version": BUILDER_VERSION,
        }),
        consumer_context=deep_freeze({}),
        disclosures=(USER_DECISION_BOUNDARY,),
    )
    return replace(provisional, snapshot_hash=compute_snapshot_hash(provisional))
