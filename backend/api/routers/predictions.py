from fastapi import APIRouter, Query
from services.prediction_engine import PredictionEngine
from typing import Literal

router = APIRouter()
engine = PredictionEngine()


@router.get("/{symbol}")
async def get_prediction(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query(
        "short",
        description="short=1-10 days | medium=1-3 months | long=6 months-3 years",
    ),
):
    """
    Returns a structured prediction with:
    - signal: BUY / HOLD / SELL
    - confidence: 0-100
    - target_price: float
    - reasoning: list of factors
    """
    return await engine.predict(symbol.upper(), market, horizon)
