"""
Market Leadership and Trend Context API — additive, flag-gated, read-only.

GET /api/leadership/context?symbol=&market= returns the canonical
structured context (RS + trend + Why Now) for one symbol. No existing
endpoint's schema changes. Never recomputes on every request beyond what
orchestration.py itself does (reads persisted snapshots where available);
the flag gate (MARKET_LEADERSHIP_UI_ENABLED) means an unauthorized/disabled
deployment answers a fixed {"status": "disabled"} shape with zero backend
work — the master ENGINE_ENABLED gate inside orchestration.py provides a
second, independent layer of the same protection.
"""
import logging
import math

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from typing import Literal

from services.market_leadership import configuration as cfg
from services.safe_errors import safe_error_message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/leadership", tags=["Market Leadership"])


def _safe_json(obj):
    """Recursively replace non-finite floats (NaN/Inf) with None.

    Defense in depth, mirroring validation.py's own `_safe_json`: the
    calculation layer already guards against provider-incomplete bars
    (services.market_leadership.sessions.drop_incomplete_bars — added
    after a live-data check found yfinance can return a NaN Close for
    the technically most-recent session), but this boundary must never
    depend on every future calculation path remembering that discipline.
    A raw NaN/Inf reaching json.dumps() raises ValueError and breaks the
    whole response — confirmed live before this guard existed.
    """
    if isinstance(obj, dict):
        return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_json(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


@router.get("/context")
def get_stock_context(
    symbol: str = Query(..., min_length=1, max_length=32),
    market: Literal["IN", "US"] = Query(...),
):
    """Shadow-only structured context for one stock. Market is required and
    explicit — never inferred from any other request state."""
    if not cfg.ui_enabled():
        return JSONResponse(content={"status": "disabled"})

    from services.market_leadership.orchestration import compute_stock_context

    try:
        result = compute_stock_context(symbol.upper(), market)
        return JSONResponse(content=_safe_json(result))
    except Exception as e:
        return JSONResponse(content={
            "status": "error",
            "error": safe_error_message(log, "leadership.get_stock_context", e,
                                        "Market Leadership context is temporarily unavailable."),
        })
