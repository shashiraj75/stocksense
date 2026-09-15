import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from services.screener_service import ScreenerService
from services.heatmap_service import get_heatmap
from services.safe_errors import safe_error_message
from services.auth import require_owner
from services.rate_limit import USER_DATA_RATE_LIMIT, limiter
from typing import Literal, Optional
import yfinance as yf

log = logging.getLogger(__name__)

router = APIRouter()
svc = ScreenerService()

CRYPTO_UNIVERSE = ["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD",
                   "DOGE-USD","ADA-USD","AVAX-USD","LINK-USD","DOT-USD"]
CRYPTO_NAMES = {"BTC":"Bitcoin","ETH":"Ethereum","BNB":"BNB","SOL":"Solana",
                "XRP":"XRP","DOGE":"Dogecoin","ADA":"Cardano","AVAX":"Avalanche",
                "LINK":"Chainlink","DOT":"Polkadot"}

_crypto_cache: tuple[float, dict] | None = None
_crypto_lock = threading.Lock()
_CRYPTO_TTL = 60  # seconds


def _fetch_crypto(yf_sym: str) -> dict:
    sym = yf_sym.replace("-USD", "")
    try:
        fi = yf.Ticker(yf_sym).fast_info
        price = round(float(fi.last_price), 4) if fi.last_price else None
        prev  = round(float(fi.previous_close), 4) if fi.previous_close else None
        change_pct = round((price - prev) / prev * 100, 2) if price and prev else 0
        return {"symbol": sym, "name": CRYPTO_NAMES.get(sym, sym), "price": price, "change_pct": change_pct}
    except Exception:
        return {"symbol": sym, "name": CRYPTO_NAMES.get(sym, sym), "price": None, "change_pct": 0}


@router.get("/crypto-movers")
async def crypto_movers():
    global _crypto_cache
    with _crypto_lock:
        if _crypto_cache and (time.time() - _crypto_cache[0]) < _CRYPTO_TTL:
            return _crypto_cache[1]

    with ThreadPoolExecutor(max_workers=len(CRYPTO_UNIVERSE)) as pool:
        results = list(pool.map(_fetch_crypto, CRYPTO_UNIVERSE))

    response = {"movers": results}
    with _crypto_lock:
        _crypto_cache = (time.time(), response)
    return response


@router.get("/top-movers")
async def top_movers(market: Literal["US", "IN"] = Query("US")):
    return await svc.get_top_movers(market)


@router.get("/heatmap")
async def heatmap(market: Literal["US", "IN"] = Query("IN")):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        sectors = await loop.run_in_executor(None, get_heatmap, market)
        return {"sectors": sectors}
    except Exception as e:
        return {"sectors": [], "error": safe_error_message(
            log, "screener.heatmap", e, "Heatmap data is temporarily unavailable.")}


@router.get("/filter")
async def filter_stocks(
    market: Literal["US", "IN"] = Query("US"),
    sector: Optional[str] = None,
    min_market_cap: Optional[float] = None,
    max_market_cap: Optional[float] = None,
    max_pe: Optional[float] = None,
    min_roe: Optional[float] = None,
    min_roce: Optional[float] = None,
    max_debt_to_equity: Optional[float] = None,
    min_sales_growth_3y: Optional[float] = None,
    min_profit_growth_3y: Optional[float] = None,
    min_business_quality_score: Optional[float] = None,
):
    """
    2026-09-15: rewritten to filter the same weekly-refreshed fundamentals
    cache query_screen's fixed Multibagger screens already use, instead of
    this endpoint's previous implementation — a live yf.Ticker(...).info
    loop across the entire market universe on every request (slow,
    un-cached, and confirmed via direct frontend code search to never have
    been wired to any UI). Instant now; also fixes a dead `signal` param
    that was accepted but never actually applied to any filter."""
    import asyncio
    from services import fundamentals_cache as cache
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(
            None,
            lambda: cache.query_filtered(
                market=market, sector=sector,
                min_market_cap=min_market_cap, max_market_cap=max_market_cap,
                max_pe=max_pe, min_roe=min_roe, min_roce=min_roce,
                max_debt_to_equity=max_debt_to_equity,
                min_sales_growth_3y=min_sales_growth_3y,
                min_profit_growth_3y=min_profit_growth_3y,
                min_business_quality_score=min_business_quality_score,
            ),
        )
        return {"market": market, "results": results, "last_refreshed": cache.last_refreshed(market)}
    except Exception as e:
        return {"market": market, "results": [], "error": safe_error_message(
            log, "screener.filter", e, "Screener data is temporarily unavailable.")}


# ══ Saved Screens ════════════════════════════════════════════════════════════
# Same Postgres-primary/JSON-file-fallback pattern as api/routers/watchlist.py
# (a named, per-user saved filter — reuses that module's own proven shape
# rather than inventing a new persistence convention).

_SAVED_SCREENS_FILE = os.path.join(os.path.dirname(__file__), "../../saved_screens_store.json")
_USE_PG = os.getenv("USE_POSTGRES") == "1"


class SavedScreenFilters(BaseModel):
    sector: Optional[str] = None
    min_market_cap: Optional[float] = None
    max_market_cap: Optional[float] = None
    max_pe: Optional[float] = None
    min_roe: Optional[float] = None
    min_roce: Optional[float] = None
    max_debt_to_equity: Optional[float] = None
    min_sales_growth_3y: Optional[float] = None
    min_profit_growth_3y: Optional[float] = None
    min_business_quality_score: Optional[float] = None


class SavedScreen(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    market: Literal["US", "IN"]
    filters: SavedScreenFilters


def _pg_get_screens(user_id: str) -> list[dict]:
    from services.postgres_store import _get_pool
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT id, name, market, filters, created_at FROM saved_screens "
            "WHERE user_id = %s ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [{"id": r[0], "name": r[1], "market": r[2], "filters": r[3],
              "created_at": r[4].isoformat() if r[4] else None} for r in rows]


def _pg_add_screen(user_id: str, screen: SavedScreen) -> int:
    from services.postgres_store import _get_pool
    with _get_pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO saved_screens (user_id, name, market, filters) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, screen.name, screen.market, json.dumps(screen.filters.model_dump())),
        ).fetchone()
    return row[0]


def _pg_remove_screen(user_id: str, screen_id: int) -> None:
    from services.postgres_store import _get_pool
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM saved_screens WHERE user_id = %s AND id = %s",
            (user_id, screen_id),
        )


def _file_load_screens() -> dict[str, list]:
    try:
        if os.path.exists(_SAVED_SCREENS_FILE):
            with open(_SAVED_SCREENS_FILE) as f:
                return json.load(f)
    except Exception as e:
        log.warning("Failed to load saved_screens store: %s", e)
    return {}


def _file_save_screens(data: dict[str, list]) -> None:
    try:
        with open(_SAVED_SCREENS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.error("Failed to save saved_screens store: %s", e)


@router.get("/saved/{user_id}")
@limiter.limit(USER_DATA_RATE_LIMIT)
async def get_saved_screens(request: Request, user_id: str, _owner: str = Depends(require_owner)):
    if _USE_PG:
        try:
            return {"screens": _pg_get_screens(user_id)}
        except Exception as e:
            log.warning("Postgres saved_screens get failed, using file: %s", e)
    return {"screens": _file_load_screens().get(user_id, [])}


@router.post("/saved/{user_id}")
@limiter.limit(USER_DATA_RATE_LIMIT)
async def add_saved_screen(request: Request, user_id: str, screen: SavedScreen, _owner: str = Depends(require_owner)):
    if _USE_PG:
        try:
            screen_id = _pg_add_screen(user_id, screen)
            return {"message": "Saved", "id": screen_id}
        except Exception as e:
            log.warning("Postgres saved_screens add failed, using file: %s", e)
    data = _file_load_screens()
    items = data.setdefault(user_id, [])
    next_id = (max((i["id"] for i in items), default=0)) + 1
    items.append({"id": next_id, "name": screen.name, "market": screen.market,
                   "filters": screen.filters.model_dump(), "created_at": None})
    _file_save_screens(data)
    return {"message": "Saved", "id": next_id}


@router.delete("/saved/{user_id}/{screen_id}")
@limiter.limit(USER_DATA_RATE_LIMIT)
async def remove_saved_screen(request: Request, user_id: str, screen_id: int, _owner: str = Depends(require_owner)):
    if _USE_PG:
        try:
            _pg_remove_screen(user_id, screen_id)
            return {"message": "Removed"}
        except Exception as e:
            log.warning("Postgres saved_screens remove failed, using file: %s", e)
    data = _file_load_screens()
    if user_id not in data:
        raise HTTPException(status_code=404, detail="No saved screens for this user")
    data[user_id] = [i for i in data[user_id] if i["id"] != screen_id]
    _file_save_screens(data)
    return {"message": "Removed"}
