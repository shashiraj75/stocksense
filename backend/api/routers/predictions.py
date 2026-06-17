import asyncio
import logging
import math
import time
import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from services.prediction_engine import PredictionEngine, _pred_cache, _PRED_TTL
from services.crypto_engine import predict_crypto
from typing import Literal

log = logging.getLogger(__name__)

router = APIRouter()
engine = PredictionEngine()

# Track which predictions are currently computing to avoid duplicate background tasks
_computing: set[str] = set()


def _to_python(obj):
    """Recursively convert numpy/pandas types to plain Python — prevents JSON serialization errors."""
    if isinstance(obj, dict):
        return {k: _to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_python(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


async def _compute_in_background(sym: str, market: str, horizon: str, key: str) -> None:
    """Runs prediction in background so the HTTP request can return immediately."""
    try:
        await engine.predict(sym, market, horizon)
        log.info("[bg-predict] completed %s", key)
    except Exception:
        log.exception("[bg-predict] failed for %s", key)
    finally:
        _computing.discard(key)


@router.get("/{symbol}")
async def get_prediction(
    symbol: str,
    market: Literal["US", "IN", "CRYPTO"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    sym = symbol.upper()

    # CRYPTO goes through a different engine — no background-task pattern needed
    if market == "CRYPTO":
        try:
            result = await predict_crypto(sym, horizon)
            return JSONResponse(content=_to_python(result))
        except BaseException:
            log.exception("Crypto prediction failed for %s", sym)
            return JSONResponse(status_code=500, content={"error": "Prediction failed. Please try again."})

    key = f"{sym}:{market}:{horizon}"

    # ── 1. Cache hit — return instantly, no compute needed ──────────────────
    cached = _pred_cache.get(key)
    if cached and (time.time() - cached[0]) < _PRED_TTL:
        return JSONResponse(content=_to_python(cached[1]))

    # ── 2. Already computing — tell client to poll back in 5 s ──────────────
    if key in _computing:
        return JSONResponse(status_code=202, content={"status": "computing", "retry_after": 5})

    # ── 3. Start background computation; return 202 immediately ─────────────
    # This ensures Render's 30-second proxy timeout is never hit.
    _computing.add(key)
    asyncio.create_task(_compute_in_background(sym, market, horizon, key))
    return JSONResponse(status_code=202, content={"status": "computing", "retry_after": 5})
