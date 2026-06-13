from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

router = APIRouter()

# In-memory store — replace with DB in production
_watchlists: dict[str, list] = {}


class WatchlistItem(BaseModel):
    symbol: str
    market: Literal["US", "IN"]
    notes: str = ""


@router.get("/{user_id}")
async def get_watchlist(user_id: str):
    return {"items": _watchlists.get(user_id, [])}


@router.post("/{user_id}")
async def add_to_watchlist(user_id: str, item: WatchlistItem):
    if user_id not in _watchlists:
        _watchlists[user_id] = []
    _watchlists[user_id].append(item.model_dump())
    return {"message": "Added", "item": item}


@router.delete("/{user_id}/{symbol}")
async def remove_from_watchlist(user_id: str, symbol: str):
    if user_id not in _watchlists:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    _watchlists[user_id] = [i for i in _watchlists[user_id] if i["symbol"] != symbol]
    return {"message": "Removed"}
