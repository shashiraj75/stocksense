import asyncio
import logging
import math
import threading
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


def _bg_thread(sym: str, market: str, horizon: str, key: str) -> None:
    """
    Run prediction in a real OS thread with its own event loop.
    asyncio.create_task() gets cancelled by anyio when the HTTP request ends;
    a daemon thread is fully independent of the request lifecycle.
    Always writes something to _pred_cache so polling resolves with a real response.
    """
    try:
        asyncio.run(engine.predict(sym, market, horizon))
        # predict() caches successes AND data-errors internally now
        log.info("[bg-predict] completed %s | cached=%s", key, key in _pred_cache)
    except Exception as e:
        # Unexpected crash — write a short-lived error so polling resolves (not infinite 202)
        log.exception("[bg-predict] exception for %s: %s", key, e)
        err = {"error": "Prediction failed — server error. Please retry in a moment."}
        _short_ts = time.time() - (_PRED_TTL - 120)  # expires in 2 min
        from services.prediction_engine import _cache_set
        _cache_set(_pred_cache, key, (_short_ts, err))
    finally:
        _computing.discard(key)


@router.get("/debug/state")
async def debug_state():
    """Debug: show current cache keys and computing set."""
    import time as t
    return {
        "computing": list(_computing),
        "cache_keys": list(_pred_cache.keys()),
        "cache_ages_s": {k: round(t.time() - v[0]) for k, v in _pred_cache.items()},
        "thread_count": threading.active_count(),
    }


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

    # ── 3. Spawn a daemon thread; return 202 immediately ────────────────────
    # Using a real OS thread (not asyncio.create_task) because anyio cancels
    # coroutine tasks when the HTTP request scope exits, which would abort the
    # background prediction. A daemon thread lives independently of requests.
    _computing.add(key)
    t = threading.Thread(target=_bg_thread, args=(sym, market, horizon, key), daemon=True)
    t.start()
    return JSONResponse(status_code=202, content={"status": "computing", "retry_after": 5})
