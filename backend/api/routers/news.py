from fastapi import APIRouter, Query
from services.news_sentiment import NewsSentimentService
from typing import Literal

router = APIRouter()
svc = NewsSentimentService()


@router.get("/{symbol}")
async def get_news_sentiment(
    symbol: str,
    market: Literal["US", "IN"] = Query("US"),
    limit: int = Query(20, le=50),
):
    """Returns latest news articles with FinBERT sentiment scores."""
    return await svc.get_news_with_sentiment(symbol.upper(), market, limit)


@router.get("/macro/us")
async def get_us_macro_news():
    return await svc.get_macro_news("US")


@router.get("/macro/india")
async def get_india_macro_news():
    return await svc.get_macro_news("IN")
