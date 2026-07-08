"""
AI Equity Research Analyst — Phase 2: /predict response composer.

The single approved API-boundary glue module (Phase 2 spec §6), mirroring
`recommendation_consolidation_api_composer.py` line-for-line in
discipline:

  - invoked ONLY by the live `/predict` route (`api/routers/predictions.py`),
    the one approved production call site — enforced by the Phase 1
    non-interference regression test's exactly-one-importer assertion;
  - NEVER mutates the prediction result it receives (which may be the
    SAME object reference stored in the shared `_pred_cache`) — the
    Phase 1 builder deep-copies its input up front, and this module's
    only construction is a new top-level dict;
  - performs no network, provider, database, filesystem, or
    PredictionEngine call; reads `os.getenv` ONLY for its own feature
    flag (the same single documented exception the RCI composer carries);
  - fails open, Option A: on ANY failure — including a validator
    rejection — it returns the ORIGINAL `prediction_result` reference
    unchanged with no `research_report` key at all, logging one bounded
    line with no stack trace or internal detail exposed to the caller.
"""

from __future__ import annotations

import logging
import os

from services.research_analyst.report_assembler import (
    ReportRejection,
    assemble_research_report,
    report_to_dict,
)
from services.research_analyst.snapshot_builder import build_live_intelligence_snapshot

log = logging.getLogger(__name__)

_ENV_VAR = "RESEARCH_ANALYST_V2_ENABLED"


def research_analyst_v2_enabled() -> bool:
    """Feature flag for the Phase 2 API integration — a single global
    flag (no market split; the composer applies no market-specific
    numeric influence to gate). Defaults to DISABLED and is absent from
    every committed configuration; enabling it in production is a
    separate future operational decision. Fails safe on any malformed
    value, mirroring every kill-switch precedent in this codebase."""
    raw = os.getenv(_ENV_VAR, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def compose_prediction_response_with_research(
    prediction_result: dict,
    *,
    symbol: str,
    market: str,
    horizon: str,
) -> dict:
    """The approved integration boundary. Accepts an already-computed
    `predict()`-shaped dict and returns a NEW dict with one additive
    `research_report` key, or the original reference unchanged on any
    failure or when the flag is off.

    The flag check here is defense-in-depth: the router gates the call
    on the same flag, and either control alone suffices (the proven
    dual-control pattern from the Growth Intelligence integration)."""
    if not research_analyst_v2_enabled():
        return prediction_result
    try:
        snapshot = build_live_intelligence_snapshot(
            prediction_result, symbol=symbol, market=market, horizon=horizon)
        report = assemble_research_report(snapshot)
        if isinstance(report, ReportRejection):
            # Fail-closed for ungrounded research, fail-open for the API:
            # the base prediction is the one guaranteed-safe thing to return.
            log.warning("[research_analyst] report rejected for %s:%s:%s rules=%s",
                        symbol, market, horizon, ",".join(report.rule_ids[:8]))
            return prediction_result
        payload = report_to_dict(report)
    except BaseException as e:
        # No stack trace or internal detail escapes to the API caller.
        log.warning("[research_analyst] composition failed for %s:%s:%s: %s",
                    symbol, market, horizon, e)
        return prediction_result
    return {**prediction_result, "research_report": payload}
