import json
import os
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

router = APIRouter()
log = logging.getLogger(__name__)

_WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "../../watchlist_store.json")


def _load() -> dict[str, list]:
    try:
        if os.path.exists(_WATCHLIST_FILE):
            with open(_WATCHLIST_FILE) as f:
                return json.load(f)
    except Exception as e:
        log.warning("Failed to load watchlist store: %s", e)
    return {}


def _save(data: dict[str, list]) -> None:
    try:
        with open(_WATCHLIST_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log.error("Failed to save watchlist store: %s", e)


class WatchlistItem(BaseModel):
    symbol: str
    market: Literal["US", "IN"]
    notes: str = ""


@router.get("/{user_id}")
async def get_watchlist(user_id: str):
    return {"items": _load().get(user_id, [])}


@router.post("/{user_id}")
async def add_to_watchlist(user_id: str, item: WatchlistItem):
    data = _load()
    if user_id not in data:
        data[user_id] = []
    # Avoid duplicate symbols
    if not any(i["symbol"] == item.symbol and i["market"] == item.market for i in data[user_id]):
        data[user_id].append(item.model_dump())
        _save(data)
    return {"message": "Added", "item": item}


@router.delete("/{user_id}/{symbol}")
async def remove_from_watchlist(user_id: str, symbol: str):
    data = _load()
    if user_id not in data:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    data[user_id] = [i for i in data[user_id] if i["symbol"] != symbol]
    _save(data)
    return {"message": "Removed"}
