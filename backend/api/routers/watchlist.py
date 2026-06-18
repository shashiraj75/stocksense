import json
import os
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

router = APIRouter()
log = logging.getLogger(__name__)

# Legacy JSON file — only used when Postgres is unavailable
_WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "../../watchlist_store.json")
_USE_PG = os.getenv("USE_POSTGRES") == "1"


# ── Postgres helpers ──────────────────────────────────────────────────────────

def _pg_get(user_id: str) -> list[dict]:
    from services.postgres_store import _get_pool
    with _get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT symbol, market, notes FROM watchlist WHERE user_id = %s ORDER BY added_at",
            (user_id,),
        ).fetchall()
    return [{"symbol": r[0], "market": r[1], "notes": r[2]} for r in rows]


def _pg_add(user_id: str, symbol: str, market: str, notes: str) -> None:
    from services.postgres_store import _get_pool
    with _get_pool().connection() as conn:
        conn.execute(
            """INSERT INTO watchlist (user_id, symbol, market, notes)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (user_id, symbol, market) DO NOTHING""",
            (user_id, symbol, market, notes),
        )


def _pg_remove(user_id: str, symbol: str) -> None:
    from services.postgres_store import _get_pool
    with _get_pool().connection() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = %s AND symbol = %s",
            (user_id, symbol),
        )


# ── JSON file fallback ────────────────────────────────────────────────────────

def _file_load() -> dict[str, list]:
    try:
        if os.path.exists(_WATCHLIST_FILE):
            with open(_WATCHLIST_FILE) as f:
                return json.load(f)
    except Exception as e:
        log.warning("Failed to load watchlist store: %s", e)
    return {}


def _file_save(data: dict[str, list]) -> None:
    try:
        with open(_WATCHLIST_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.error("Failed to save watchlist store: %s", e)


# ── Pydantic model ────────────────────────────────────────────────────────────

class WatchlistItem(BaseModel):
    symbol: str
    market: Literal["US", "IN"]
    notes: str = ""


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{user_id}")
async def get_watchlist(user_id: str):
    if _USE_PG:
        try:
            return {"items": _pg_get(user_id)}
        except Exception as e:
            log.warning("Postgres watchlist get failed, using file: %s", e)
    return {"items": _file_load().get(user_id, [])}


@router.post("/{user_id}")
async def add_to_watchlist(user_id: str, item: WatchlistItem):
    if _USE_PG:
        try:
            _pg_add(user_id, item.symbol, item.market, item.notes)
            return {"message": "Added", "item": item}
        except Exception as e:
            log.warning("Postgres watchlist add failed, using file: %s", e)
    # File fallback
    data = _file_load()
    if user_id not in data:
        data[user_id] = []
    if not any(i["symbol"] == item.symbol and i["market"] == item.market for i in data[user_id]):
        data[user_id].append(item.model_dump())
        _file_save(data)
    return {"message": "Added", "item": item}


@router.delete("/{user_id}/{symbol}")
async def remove_from_watchlist(user_id: str, symbol: str):
    if _USE_PG:
        try:
            _pg_remove(user_id, symbol)
            return {"message": "Removed"}
        except Exception as e:
            log.warning("Postgres watchlist remove failed, using file: %s", e)
    # File fallback
    data = _file_load()
    if user_id not in data:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    data[user_id] = [i for i in data[user_id] if i["symbol"] != symbol]
    _file_save(data)
    return {"message": "Removed"}
