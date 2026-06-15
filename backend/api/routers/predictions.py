import math
import traceback
import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from services.prediction_engine import PredictionEngine
from services.crypto_engine import predict_crypto
from typing import Literal

router = APIRouter()
engine = PredictionEngine()


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


@router.get("/{symbol}")
async def get_prediction(
    symbol: str,
    market: Literal["US", "IN", "CRYPTO"] = Query("US"),
    horizon: Literal["short", "medium", "long"] = Query("short"),
):
    try:
        sym = symbol.upper()
        if market == "CRYPTO":
            result = await predict_crypto(sym, horizon)
        else:
            result = await engine.predict(sym, market, horizon)
        return JSONResponse(content=_to_python(result))
    except BaseException as e:
        tb = traceback.format_exc()
        return JSONResponse(status_code=500, content={"error": str(e), "trace": tb})
