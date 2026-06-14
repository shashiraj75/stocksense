from fastapi import APIRouter, Query
from services.prediction_engine import PredictionEngine
from services.crypto_engine import predict_crypto
from typing import Literal

router = APIRouter()
engine = PredictionEngine()


@router.get("/{symbol}")
async def get_prediction(
    symbol: str,
    market: Literal["US", "IN", "CRYPTO"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    sym = symbol.upper()
    if market == "CRYPTO":
        return await predict_crypto(sym, horizon)
    return await engine.predict(sym, market, horizon)
