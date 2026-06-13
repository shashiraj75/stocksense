import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_vader = SentimentIntensityAnalyzer()

# Free public RSS feeds — no API key needed
RSS_FEEDS = {
    "US": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
        "https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en",
    ],
    "IN": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}.NS&region=IN&lang=en-IN",
        "https://news.google.com/rss/search?q={symbol}+NSE+India+stock&hl=en-IN&gl=IN&ceid=IN:en",
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/{symbol}.cms",
    ],
}

MACRO_FEEDS = {
    "US": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
        "https://news.google.com/rss/search?q=US+Federal+Reserve+interest+rates+economy&hl=en-US&gl=US&ceid=US:en",
    ],
    "IN": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5ENSEI&region=IN&lang=en-IN",
        "https://news.google.com/rss/search?q=RBI+India+Nifty+Sensex+economy&hl=en-IN&gl=IN&ceid=IN:en",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    ],
}


def score_sentiment(text: str) -> dict:
    scores = _vader.polarity_scores(text)
    compound = scores["compound"]
    if compound >= 0.05:
        label = "BULLISH"
    elif compound <= -0.05:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    return {"label": label, "score": round(abs(compound), 3)}


class NewsSentimentService:
    async def get_news_with_sentiment(self, symbol: str, market: str, limit: int = 20) -> dict:
        articles = self._fetch_rss(symbol, market, limit)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"])
        return {"symbol": symbol, "market": market, "articles": articles}

    async def get_macro_news(self, market: str) -> dict:
        articles = self._fetch_macro_rss(market, 15)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"])
        return {"market": market, "articles": articles}

    def _fetch_rss(self, symbol: str, market: str, limit: int) -> list:
        articles = []
        for url_template in RSS_FEEDS.get(market, RSS_FEEDS["US"]):
            url = url_template.format(symbol=symbol)
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:limit]:
                    articles.append({
                        "title": entry.get("title", ""),
                        "source": feed.feed.get("title", "RSS"),
                        "url": entry.get("link", "#"),
                        "published_at": entry.get("published", ""),
                        "description": entry.get("summary", "")[:200],
                    })
            except Exception:
                pass
            if len(articles) >= limit:
                break
        return articles[:limit]

    def _fetch_macro_rss(self, market: str, limit: int) -> list:
        articles = []
        for url in MACRO_FEEDS.get(market, []):
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:limit]:
                    articles.append({
                        "title": entry.get("title", ""),
                        "source": feed.feed.get("title", "RSS"),
                        "url": entry.get("link", "#"),
                        "published_at": entry.get("published", ""),
                        "description": entry.get("summary", "")[:200],
                    })
            except Exception:
                pass
        return articles[:limit]
