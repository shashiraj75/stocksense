import asyncio
import logging
import math
import os
import threading
import time
import numpy as np
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from services.prediction_engine import PredictionEngine, _pred_cache, _PRED_TTL
from services.crypto_engine import predict_crypto
from services.recommendation_consolidation_api_composer import (
    compose_prediction_response_with_rci, rci_live_stock_analysis_enabled,
)
from services.recommendation_consolidation_contract import is_valid_rci_response_payload
from services.research_analyst.research_composer import (
    compose_prediction_response_with_research, research_analyst_v2_enabled,
)
from typing import Literal

log = logging.getLogger(__name__)

# Release 14B: reuses the exact same internal secret-management model as
# /api/picks/generate's proven X-Secret pattern (api/routers/picks.py) — same
# env var, same header name, same comparison, same 401-with-no-body-detail
# shape. No new secret is introduced; PICKS_SECRET is read independently
# here (not imported from picks.py) to keep this a single-file, isolated
# change with zero coupling to — and zero risk of altering — the existing
# Daily Picks trigger route's own protection.
_DEBUG_SECRET = os.getenv("PICKS_SECRET", "")

router = APIRouter()
engine = PredictionEngine()

# Track which predictions are currently computing to avoid duplicate background tasks
_computing: set[str] = set()

# Circular log buffer — visible via /debug/state so we can inspect without Render logs
_bg_log: list[str] = []
def _bglog(msg: str) -> None:
    _bg_log.append(msg)
    if len(_bg_log) > 30:
        _bg_log.pop(0)
    print(msg, flush=True)


# RCI observability (Epic 006, Release 13C) — process-local aggregate counters
# only, visible via /debug/state, same tier as _bg_log above. No persistence,
# no external network calls; resets on process restart. Guarded by a lock
# since this router serves concurrent requests and _bg_thread runs on real
# OS threads (not just asyncio tasks), so a bare `+= 1` isn't guaranteed atomic.
_rci_composition_success_count = 0
_rci_fail_open_count = 0
_rci_counter_lock = threading.Lock()


def _record_rci_outcome(success: bool) -> None:
    """Increment the appropriate RCI counter. Callers must wrap this in their
    own try/except — this function intentionally does not swallow errors
    itself, so a broken counter can never silently mask a real bug here."""
    global _rci_composition_success_count, _rci_fail_open_count
    with _rci_counter_lock:
        if success:
            _rci_composition_success_count += 1
        else:
            _rci_fail_open_count += 1


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


def _fresh_cached_prediction(key: str) -> dict | None:
    """The single cache-freshness check shared by /{symbol} and
    /{symbol}/signal — both routes must read `_pred_cache` identically so a
    warm hit can never be fresh on one route and stale on the other."""
    cached = _pred_cache.get(key)
    if cached and (time.time() - cached[0]) < _PRED_TTL:
        return cached[1]
    return None


def _start_background_compute(sym: str, market: str, horizon: str, key: str) -> None:
    """Register `key` as computing and spawn the daemon-thread compute.
    Shared by /{symbol} and /{symbol}/signal so there is exactly one way a
    prediction gets computed, regardless of which route triggered it."""
    _computing.add(key)
    t = threading.Thread(target=_bg_thread, args=(sym, market, horizon, key), daemon=True)
    t.start()


def _signal_summary(result: dict, sym: str, market: str, horizon: str) -> dict:
    """Sprint 011 (§20.1) — minimal payload for consumers that only need the
    signal badge (Portfolio). Values are read from the SAME cached dict the
    full route serves, never recomputed, so the two routes cannot disagree.
    Error-cached entries carry no `signal`/`confidence` keys and surface as
    nulls — the badge's existing "no signal" state."""
    return {
        "symbol": sym,
        "market": market,
        "horizon": horizon,
        "signal": result.get("signal"),
        "confidence": result.get("confidence"),
    }


def _bg_thread(sym: str, market: str, horizon: str, key: str) -> None:
    """
    Run prediction in a real OS thread with its own event loop.
    asyncio.create_task() gets cancelled by anyio when the HTTP request ends;
    a daemon thread is fully independent of the request lifecycle.
    Always writes something to _pred_cache so polling resolves with a real response.
    """
    _bglog(f"[bg_thread] START {key} thread_id={threading.get_ident()}")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(engine.predict(sym, market, horizon))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        _bglog(f"[bg_thread] DONE {key} result_keys={list(result.keys()) if result else None} in_cache={key in _pred_cache} cache_size={len(_pred_cache)}")
        # predict() caches successes AND data-errors internally now
        if key not in _pred_cache:
            # Shouldn't happen — but guarantee cache is written
            from services.prediction_engine import _cache_set
            _short_ts = time.time() - (_PRED_TTL - 120)
            _cache_set(_pred_cache, key, (_short_ts, result or {"error": "No result"}))
            _bglog(f"[bg_thread] FORCE-CACHED {key}")
    except Exception as e:
        import traceback
        _bglog(f"[bg_thread] EXCEPTION {key}: {type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")
        log.exception(f"[predictions.bg_thread] prediction failed for {key}")
        err = {"error": "Prediction data is temporarily unavailable. Please try again."}
        _short_ts = time.time() - (_PRED_TTL - 120)
        from services.prediction_engine import _cache_set
        _cache_set(_pred_cache, key, (_short_ts, err))
    finally:
        _computing.discard(key)
        _bglog(f"[bg_thread] FINALLY {key} in_cache={key in _pred_cache}")


@router.get("/debug/state")
async def debug_state(x_secret: str = Header(None)):
    """
    Aggregate-only operational snapshot. Release 14B: authenticated (same
    X-Secret model as /api/picks/generate) and permanently narrowed — no
    raw cache keys, no per-symbol/market/horizon identifiers, no background
    log lines (which previously leaked full internal prediction-result
    schema), no error/exception text. Only counts and summary statistics
    that are safe to view without revealing traffic patterns or internals.

    Fails CLOSED (401) rather than /api/picks/generate's plain `!=`
    comparison: an unconfigured or blank _DEBUG_SECRET must never let a
    blank/absent header "match" it. Both sides are required to be a real,
    non-empty, non-whitespace value before any comparison is attempted.
    """
    configured = (_DEBUG_SECRET or "").strip()
    provided = (x_secret or "").strip()
    if not configured or not provided or provided != configured:
        raise HTTPException(status_code=401, detail="Invalid secret")

    import time as t
    ages = [t.time() - v[0] for v in _pred_cache.values()]
    return {
        "computing_count": len(_computing),
        "cache_entry_count": len(_pred_cache),
        "cache_age_summary_s": {
            "min": round(min(ages)) if ages else None,
            "max": round(max(ages)) if ages else None,
            "avg": round(sum(ages) / len(ages)) if ages else None,
        },
        "thread_count": threading.active_count(),
        "rci_observability": {
            "composition_success_count": _rci_composition_success_count,
            "fail_open_count": _rci_fail_open_count,
        },
    }


@router.get("/{symbol}/signal")
async def get_signal_summary(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    """
    Sprint 011 (§20.1) — additive, read-only signal-only endpoint. Returns
    just {signal, confidence} (plus the identifying key dimensions) sourced
    from the existing prediction cache, so consumers that only render a
    signal badge (Portfolio's per-holding column) don't have to pull the
    full multi-engine /{symbol} payload per holding.

    Contract mirrors /{symbol} exactly: warm cache -> 200 with the same
    signal/confidence values the full route would return; cold -> 202
    {status: computing, retry_after} and the same daemon-thread compute is
    started, writing to the same shared cache. /{symbol}'s own response
    shape is untouched. CRYPTO is not supported here — it bypasses
    _pred_cache entirely and has no badge-only consumer.
    """
    sym = symbol.upper()
    key = f"{sym}:{market}:{horizon}"

    result = _fresh_cached_prediction(key)
    if result is not None:
        return JSONResponse(content=_to_python(_signal_summary(result, sym, market, horizon)))

    if key not in _computing:
        _start_background_compute(sym, market, horizon, key)
    return JSONResponse(status_code=202, content={"status": "computing", "retry_after": 5})


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
    result = _fresh_cached_prediction(key)
    if result is not None:
        # Recommendation Consolidation Intelligence (Epic 005, Sprint #008) —
        # the one, approved integration boundary (Sprint #007's decision):
        # an opt-in, read-only, additive composer invoked ONLY here, in the
        # live API route, never inside PredictionEngine.predict() itself.
        # `result` here is the SAME object reference stored in `_pred_cache`
        # (confirmed, Sprint #007's own decisive finding) — the composer is
        # built to never mutate it, returning a new dict instead; `result`
        # itself, and therefore the cache entry, is never written to below.
        if rci_live_stock_analysis_enabled():
            result = compose_prediction_response_with_rci(result, symbol=sym, market=market)
            # Observability only — best-effort, must never affect the response below.
            # Release 13C fix: presence of the key alone is not success — a
            # None value, a non-dict value, a malformed dict, or an
            # unsupported contract_version must all count as fail-open, not
            # success. is_valid_rci_response_payload is the single
            # canonical-version-aware validity check (backend-local, no
            # frontend import).
            try:
                _record_rci_outcome(
                    success=is_valid_rci_response_payload(
                        result.get("recommendation_consolidation")
                    )
                )
            except Exception:
                pass
        # AI Research Analyst Phase 2 (Epic 008B) — the one approved call
        # site, mirroring the RCI composer above exactly: copy-on-read,
        # additive `research_report` key only, fail-open by returning the
        # original result reference unchanged on any failure. Runs AFTER the
        # RCI block and never reads its output as evidence (deferred
        # explicitly). Flag defaults off and is absent from every committed
        # configuration.
        if research_analyst_v2_enabled():
            result = compose_prediction_response_with_research(
                result, symbol=sym, market=market, horizon=horizon)
        return JSONResponse(content=_to_python(result))

    # ── 2. Already computing — tell client to poll back in 5 s ──────────────
    if key in _computing:
        return JSONResponse(status_code=202, content={"status": "computing", "retry_after": 5})

    # ── 3. Spawn a daemon thread; return 202 immediately ────────────────────
    # Using a real OS thread (not asyncio.create_task) because anyio cancels
    # coroutine tasks when the HTTP request scope exits, which would abort the
    # background prediction. A daemon thread lives independently of requests.
    _start_background_compute(sym, market, horizon, key)
    return JSONResponse(status_code=202, content={"status": "computing", "retry_after": 5})
