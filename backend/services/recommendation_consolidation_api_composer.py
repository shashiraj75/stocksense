"""
Recommendation Consolidation Intelligence — Live Stock Analysis API Composer
(Epic 005, Sprint #008).

The single, approved integration boundary per Sprint #007's own decision:
a dedicated, read-only response composer invoked ONLY by the live
`/predict` API route. Per that sprint's decisive finding — `predict()`'s
cache-hit path returns its cached dict BY REFERENCE, and Daily Picks
shares that exact same `_pred_cache` object via direct `engine.predict()`
calls — this module is built around one absolute rule: it NEVER mutates
the prediction result it receives, it only ever returns a NEW dict.

This module:
  - is never imported by prediction_engine.py, daily_picks.py, any
    individual engine, or any persistence/database layer (confirmed by
    the regression suite, mirroring the exact static-import-absence
    pattern Sprints #003-#005 already established for RCI's own core);
  - performs no network, provider, database, or filesystem call;
  - reads `os.getenv` ONLY for its own feature flag (the one,
    deliberate exception to RCI's pure core's "no os import" rule,
    confirmed safe because this module is API-composition glue, not
    part of the pure consolidation core itself, which remains
    untouched and still has zero such imports).
"""

import dataclasses
import logging
import os

from services.recommendation_consolidation_contract import RecommendationConsolidationResponse
from services.recommendation_consolidation_engine import compute_recommendation_consolidation
from services.recommendation_evidence_adapter import build_recommendation_evidence_snapshot

log = logging.getLogger(__name__)

_ENV_VAR = "RCI_LIVE_STOCK_ANALYSIS_ENABLED"


def rci_live_stock_analysis_enabled() -> bool:
    """
    Feature flag for this sprint's API-only integration — a single,
    global flag (not market-split, unlike the engine-level kill
    switches, since RCI itself applies no market-specific numeric
    influence to gate). Defaults to DISABLED, per this sprint's explicit
    "default must be disabled... do not enable in production this
    sprint" rule. Fails safe on any malformed value, mirroring every
    other kill-switch function already proven in this codebase
    (`_growth_intelligence_confidence_enabled`,
    `_valuation_intelligence_confidence_enabled`).
    """
    raw = os.getenv(_ENV_VAR, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _serialize_rci_response(resp: RecommendationConsolidationResponse) -> dict:
    """`dataclasses.asdict()` recursively converts the response (and its
    nested `ConflictMatch` tuples) into plain dicts/lists/strings/bools —
    already confirmed JSON-safe by direct inspection (every field is a
    `str`, `bool`, `int`, or a tuple of one of those) — no further
    `_to_python()`-style numpy/pandas conversion is needed, since RCI's
    pure core never touches numpy/pandas at all."""
    return dataclasses.asdict(resp)


def compose_prediction_response_with_rci(
    prediction_result: dict,
    *,
    symbol: str,
    market: str,
) -> dict:
    """
    The approved integration boundary (Sprint #007's decision).

    Accepts an already-computed `predict()`-shaped dict (which may be
    the SAME object reference stored in the shared `_pred_cache` —
    confirmed by Sprint #007's own Evidence Checkpoint). Returns a NEW
    dict with an additive `recommendation_consolidation` key.

    Verified safe to use a SHALLOW top-level merge here, not a deep
    copy, per this sprint's own "do not assume a shallow copy is
    sufficient... verify" instruction: the only thing this function
    constructs is a new top-level dict via `{**prediction_result, ...}`.
    The four nested engine sub-dicts (`business_quality`, etc.) remain
    the SAME object references as in the original — but neither this
    function nor anything it calls (`build_recommendation_evidence_
    snapshot`, `compute_recommendation_consolidation`) ever assigns into
    those nested dicts; they are read-only inputs throughout the entire
    RCI pure core (confirmed unchanged since Sprint #003). A deep copy
    would add real, measurable overhead (Sprint #009's own future
    perf-measurement territory, not fabricated here) to defend against a
    mutation path that does not exist in this codebase today — verified,
    not assumed, by re-reading every line of `recommendation_evidence_
    adapter.py`'s and `recommendation_consolidation_engine.py`'s own
    code, neither of which contains a single `=` assignment into any
    object it did not itself construct.

    If RCI fails at any stage (snapshot construction, adapter
    normalization, pure consolidation, or serialization), this function
    returns the ORIGINAL `prediction_result` reference UNCHANGED, with no
    `recommendation_consolidation` key added at all — Option A from this
    sprint's own §7 (omit entirely on failure), chosen over a structured
    `unavailable` marker because a genuine internal error (as opposed to
    a structurally-anticipated missing-evidence case, which RCI's own
    pure core already handles via its existing status taxonomy) should
    not manufacture a new, partially-trustworthy field at all — the base
    prediction is the one guaranteed-safe thing to return.
    """
    try:
        # Reads (never alters) the real, live Valuation Intelligence kill-
        # switch state — per this sprint's explicit "must not alter any
        # Valuation Intelligence kill-switch state" rule, this is a
        # read-only observation, the same pattern the engine's own
        # _apply_valuation_intelligence_adjustment already uses. If the
        # switch is off (the default, confirmed unchanged today), RCI's
        # evidence correctly reflects Valuation Intelligence as
        # `feature_disabled`, not silently treating it as enabled.
        from services.prediction_engine import _valuation_intelligence_confidence_enabled
        valuation_confidence_enabled = _valuation_intelligence_confidence_enabled(market)

        snapshot = build_recommendation_evidence_snapshot(
            symbol,
            market,
            business_quality=prediction_result.get("business_quality"),
            financial_strength=prediction_result.get("financial_strength"),
            growth_intelligence=prediction_result.get("growth_intelligence"),
            valuation_intelligence=prediction_result.get("valuation_intelligence"),
            valuation_confidence_enabled=valuation_confidence_enabled,
            sector_bucket=None,
        )
        rci_response = compute_recommendation_consolidation(snapshot)
        rci_payload = _serialize_rci_response(rci_response)
    except BaseException as e:
        # Fail open, per this sprint's own non-negotiable rule — never
        # let an RCI failure prevent a valid prediction from being
        # returned. No stack trace or internal detail is exposed to the
        # API caller; only a generic, safely-logged warning.
        log.warning("[recommendation_consolidation] composition failed for %s:%s: %s", symbol, market, e)
        return prediction_result

    return {**prediction_result, "recommendation_consolidation": rci_payload}
