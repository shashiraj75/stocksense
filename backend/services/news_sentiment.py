import asyncio
import logging
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

log = logging.getLogger(__name__)

_vader = SentimentIntensityAnalyzer()

# ── Freshness policy (UI/UX Truthfulness Correction Program, Wave 0C) ────────
# Current sentiment must be computed only from age-verified, eligible news.
# One central policy — do not scatter cutoffs across files. Values are
# initial conservative defaults, deliberately easy to recalibrate here.
#
# Keyed by prediction horizon; "general" covers stock-detail sentiment when
# no horizon applies. Note "long" is Daily Picks' strategic ~3-6 month
# horizon, not multi-year investing.
SENTIMENT_MAX_AGE_DAYS: dict[str, int] = {
    "short":   3,
    "medium":  14,
    "long":    30,
    "general": 14,
}

# Articles time-stamped slightly in the future (feed clock skew) are
# tolerated up to this bound; beyond it the date is treated as unparseable
# noise and the article is ineligible for current sentiment.
FUTURE_TOLERANCE = timedelta(hours=24)


def parse_published_at(raw: str | None) -> datetime | None:
    """
    Parse an RSS publication date into a UTC-aware datetime, or None.

    Handles the two formats real feeds emit: RFC-2822 ("Wed, 02 Jul 2026
    07:15:00 GMT" — Yahoo/Google News/ET/Moneycontrol) and ISO-8601.
    A naive result is assumed UTC. Never raises; never invents a date —
    missing/malformed input returns None, which downstream treats as
    ineligible for current sentiment (not as fresh, not as negative).
    """
    if not raw or not isinstance(raw, str):
        return None
    for parser in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            dt = parser(raw.strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


# ── Company-relevance qualification (Wave 0D1) ───────────────────────────────
# A fresh article is NOT automatically a company-specific article. Articles
# returned by ticker-based search feeds routinely cover peers, sectors, or
# the broad market while merely mentioning the target. This deterministic,
# auditable classifier decides whether an article is primarily about the
# target company. High precision over recall by design: only
# "company_specific" counts toward current company sentiment; every other
# class (including "unknown") is context with ZERO decision weight — unknown
# is unavailable evidence, never negative and never neutral evidence.
# No LLM, no embeddings, no external API, no new dependency.

RELEVANCE_CLASSES = (
    "company_specific", "company_related_but_secondary", "peer_company",
    "sector_relevant", "macro_market", "unrelated", "unknown",
)

# Explicit, documented company-identification alias registry — deliberately
# small, deterministic, and easy to extend. It exists ONLY for company
# identification; it is not a source-quality or scoring system. It is NOT
# exhaustive: a missing alias can only reduce news confirmation coverage for
# a stock — it can never create a bearish penalty (missing evidence is
# unavailable evidence).
_COMPANY_ALIASES: dict[str, list[str]] = {
    "SBIN": ["SBI", "State Bank of India"],
    "TCS":  ["Tata Consultancy Services"],
    "TSM":  ["TSMC", "Taiwan Semiconductor", "Taiwan Semiconductor Manufacturing"],
}

# Collective-sector phrasings: a title built around a *group* of stocks is
# about the group, not the target — even when the target is named in it
# ("AI chip stocks rally; TSM among gainers"). Small and test-covered; do
# not grow this into an untested keyword dump.
_SECTOR_COLLECTIVE_PATTERNS = [
    "ai stocks", "ai stock", "chip stocks", "chip stock", "semiconductor stocks",
    "tech stocks", "bank stocks", "banking stocks", "auto stocks",
    "pharma stocks", "it stocks", "psu stocks", "metal stocks",
]

# Macro/market patterns: index and institution mentions. These classify an
# article as macro only when NO target identifier is in the title — a real
# company story is never demoted merely for also mentioning an index.
_MACRO_PATTERNS = [
    "market rally", "stock market", "global markets", "markets today",
    "nifty", "sensex", "s&p 500", "nasdaq", "dow jones",
    "federal reserve", "interest rates", "wall street",
]

# All-caps tokens that look like tickers but are ordinary finance shorthand —
# never treated as peer-company ticker mentions.
_UPPER_TOKEN_STOPWORDS = {
    "CEO", "CFO", "CTO", "COO", "IPO", "USA", "GDP", "FED", "SEC", "ETF",
    "EPS", "NYSE", "NSE", "BSE", "THE", "AND", "FOR", "NEW", "USD", "INR",
    "API", "AGM", "FII", "DII", "YOY", "QOQ", "PAT", "RBI", "GST", "TOP",
}
_UPPER_TOKEN_RE = re.compile(r"\b[A-Z]{3,5}\b")

# Words dropped when deriving a company's "core name" from the static
# universe entry ("Tata Consultancy Services Limited" → "tata consultancy
# services").
_CORP_SUFFIX_WORDS = {
    "limited", "ltd", "inc", "corp", "corporation", "company", "co", "plc",
    "the", "common", "stock", "shares", "class", "adr", "holdings", "group",
}

_norm_re = re.compile(r"[^a-z0-9 ]+")


def _normalize_text(text: str) -> str:
    return _norm_re.sub(" ", (text or "").lower()).strip()


def _core_name_words(name: str) -> list[str]:
    words = [w for w in _normalize_text(name).split() if w not in _CORP_SUFFIX_WORDS]
    return words


@lru_cache(maxsize=1)
def _universe_index() -> dict:
    """
    Lazily-built lookup structures from the existing static stock universe —
    the canonical company-identity source (no duplicate uncontrolled list).
      tickers:      all known tickers (IN + US) for peer-ticker detection
      name_prefix:  first-two-core-words → set of tickers, for detecting a
                    *different* listed company named in a title (two-word
                    prefixes like "micron technology" / "state bank" are
                    rarely ambiguous; single words are too dangerous)
      core_names:   ticker → core-name word list, per market
    """
    from services.stock_universe import IN_STOCKS, US_STOCKS
    tickers: set[str] = set()
    name_prefix: dict[str, set[str]] = {}
    core_names: dict[tuple[str, str], list[str]] = {}
    for market, entries in (("IN", IN_STOCKS), ("US", US_STOCKS)):
        for sym, name in entries:
            tickers.add(sym)
            words = _core_name_words(name)
            core_names[(sym, market)] = words
            if len(words) >= 2:
                name_prefix.setdefault(f"{words[0]} {words[1]}", set()).add(sym)
    return {"tickers": tickers, "name_prefix": name_prefix, "core_names": core_names}


@lru_cache(maxsize=2048)
def _target_identifiers(symbol: str, market: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    (normalized name/alias phrases, target's own two-word prefixes) for one
    company. Name phrases require >= 2 core words — single-word company
    names are too collision-prone ("Target", "Apple" in generic prose) and
    rely on the ticker/alias channels instead.
    """
    idx = _universe_index()
    phrases: list[str] = []
    own_prefixes: list[str] = []
    words = idx["core_names"].get((symbol.upper(), market), [])
    if len(words) >= 2:
        phrases.append(" ".join(words))
        own_prefixes.append(f"{words[0]} {words[1]}")
    for alias in _COMPANY_ALIASES.get(symbol.upper(), []):
        a_norm = _normalize_text(alias)
        if a_norm:
            phrases.append(a_norm)
            a_words = a_norm.split()
            if len(a_words) >= 2:
                own_prefixes.append(f"{a_words[0]} {a_words[1]}")
    return tuple(phrases), tuple(own_prefixes)


def _phrase_in(norm_text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", norm_text) is not None


def _target_mentioned(raw_text: str, norm_text: str, symbol: str, market: str) -> bool:
    """Strong target identifier: safe uppercase ticker token (len >= 3,
    matched case-sensitively against the ORIGINAL text so prose words can't
    collide), or a word-boundary match of the canonical name / an alias."""
    sym = symbol.upper()
    if len(sym) >= 3 and sym not in _UPPER_TOKEN_STOPWORDS:
        if re.search(rf"\b{re.escape(sym)}\b", raw_text or ""):
            return True
    phrases, _ = _target_identifiers(sym, market)
    return any(_phrase_in(norm_text, p) for p in phrases)


def _peer_mentioned(raw_title: str, norm_title: str, symbol: str, market: str) -> bool:
    """A *different* known listed company named in the title — via a safe
    uppercase ticker token, a two-word core-name prefix, or another
    company's registry alias. Peer hits only ever demote (conservative
    direction); they never credit anything. The target's own ticker AND its
    own alias tokens are excluded — "SBI" in an SBIN headline is the target,
    even if some unrelated listed ticker happens to also be "SBI"."""
    sym = symbol.upper()
    idx = _universe_index()
    _, own_prefixes = _target_identifiers(sym, market)
    own_tokens = {sym} | {
        a.upper() for a in _COMPANY_ALIASES.get(sym, []) if " " not in a
    }
    for tok in _UPPER_TOKEN_RE.findall(raw_title or ""):
        if tok not in own_tokens and tok not in _UPPER_TOKEN_STOPWORDS and tok in idx["tickers"]:
            return True
    words = norm_title.split()
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i + 1]}"
        syms = idx["name_prefix"].get(bigram)
        if syms and sym not in syms and bigram not in own_prefixes:
            return True
    for other_sym, aliases in _COMPANY_ALIASES.items():
        if other_sym == sym:
            continue
        if any(_phrase_in(norm_title, _normalize_text(a)) for a in aliases):
            return True
    return False


def classify_article_relevance(title: str, description: str, symbol: str,
                               market: str) -> tuple[str, str]:
    """
    Deterministic company-relevance classification. Precedence (explicit and
    test-covered):
      1. target in title + collective-sector phrasing → sector_relevant
         (the title is about a group of stocks, not this company)
      2. target in title + another listed company in title
         → company_related_but_secondary
      3. target in title → company_specific
      4. peer in title (target absent) → peer_company
      5. collective-sector phrasing → sector_relevant
      6. macro/index pattern → macro_market
      7. target only in description → company_related_but_secondary
         (a description mention is never enough to be company_specific)
      8. otherwise → unknown (no reliable linkage — unavailable evidence,
         not negative and not neutral; "unrelated" is reserved for future
         provably-other-subject detection and is not assigned by v1)
    """
    norm_title = _normalize_text(title)
    norm_desc = _normalize_text(description)
    target_in_title = _target_mentioned(title, norm_title, symbol, market)
    sector_hit = any(_phrase_in(norm_title, p) for p in _SECTOR_COLLECTIVE_PATTERNS)

    if target_in_title:
        if sector_hit:
            return "sector_relevant", "title uses collective sector phrasing"
        if _peer_mentioned(title, norm_title, symbol, market):
            return "company_related_but_secondary", "another listed company is named in the title"
        return "company_specific", "target company named in the title"
    if _peer_mentioned(title, norm_title, symbol, market):
        return "peer_company", "title is about another listed company"
    if sector_hit:
        return "sector_relevant", "title uses collective sector phrasing"
    if any(_phrase_in(norm_title, p) for p in _MACRO_PATTERNS):
        return "macro_market", "title is market/macro commentary"
    if _target_mentioned(description, norm_desc, symbol, market):
        return "company_related_but_secondary", "target appears only in the description"
    return "unknown", "no reliable company linkage detected"


def classify_articles_relevance(articles: list, symbol: str, market: str) -> None:
    """Annotate articles in place with relevance_class / relevance_reason /
    company_sentiment_eligible (fresh AND company_specific)."""
    for a in articles:
        rel, reason = classify_article_relevance(a.get("title", ""), a.get("description", ""), symbol, market)
        a["relevance_class"] = rel
        a["relevance_reason"] = reason
        a["company_sentiment_eligible"] = bool(a.get("sentiment_eligible")) and rel == "company_specific"


def classify_article_freshness(published_at_raw: str | None, horizon: str | None = None,
                               now: datetime | None = None) -> tuple[bool, str]:
    """
    Classify one article against the central freshness policy.

    Returns ``(eligible, reason)`` where reason is one of:
      "fresh"        — usable as current sentiment evidence for this horizon
      "stale"        — real date, but older than the horizon's cutoff
      "invalid_date" — missing/unparseable publication date
      "future_date"  — dated beyond the clock-skew tolerance

    The reason exists for presentation metadata only — decision paths key
    exclusively on the boolean, exactly as before.
    """
    published = parse_published_at(published_at_raw)
    if published is None:
        return False, "invalid_date"
    now = now or datetime.now(timezone.utc)
    if published > now + FUTURE_TOLERANCE:
        return False, "future_date"
    max_days = SENTIMENT_MAX_AGE_DAYS.get(horizon or "general", SENTIMENT_MAX_AGE_DAYS["general"])
    if (now - published) <= timedelta(days=max_days):
        return True, "fresh"
    return False, "stale"


def is_article_eligible(published_at_raw: str | None, horizon: str | None = None,
                        now: datetime | None = None) -> bool:
    """
    Whether one article is fresh enough to count as CURRENT sentiment
    evidence for the given horizon. Undated, unparseable, too-old, or
    (beyond tolerance) future-dated articles are ineligible — they may still
    be displayed as context, but must contribute zero decision weight.
    """
    return classify_article_freshness(published_at_raw, horizon, now)[0]


def split_articles_by_freshness(
    articles: list, horizon: str | None = None, now: datetime | None = None,
) -> tuple[list, list]:
    """
    Split a fetched article list into (eligible, historical) for the given
    horizon. Identical rule for every market — freshness is a property of
    the article's timestamp only. One malformed article never rejects the
    batch; it simply lands in the historical bucket.
    """
    now = now or datetime.now(timezone.utc)
    eligible: list = []
    historical: list = []
    for a in articles:
        (eligible if is_article_eligible(a.get("published_at"), horizon, now) else historical).append(a)
    return eligible, historical

# Financial domain lexicon — terms VADER misses or scores wrong.
# Compound override: if any phrase is found in text, it anchors the score.
# Values are in VADER's compound range [-1, +1].
_FIN_LEXICON: list[tuple[str, float]] = [
    # Strongly bullish
    ("strong buy",          0.90), ("buy rating",          0.80),
    ("record profit",       0.80), ("record revenue",      0.75),
    ("beat expectations",   0.75), ("beats estimates",     0.75),
    ("dividend hike",       0.65), ("dividend increase",   0.60),
    ("upgrade",             0.65), ("upgraded",            0.65),
    ("outperform",          0.60), ("overweight",          0.50),
    ("share buyback",       0.55), ("share repurchase",    0.50),
    ("all-time high",       0.70), ("52-week high",        0.60),
    ("breakout",            0.55), ("rally",               0.50),
    ("bullish",             0.65), ("beat",                0.50),
    ("exceeds",             0.45), ("surpassed",           0.50),
    ("robust earnings",     0.70), ("strong earnings",     0.65),
    ("margin expansion",    0.55), ("order inflow",        0.50),
    ("promoter buying",     0.55), ("fii buying",          0.55),
    # Strongly bearish
    ("strong sell",        -0.90), ("sell rating",        -0.80),
    ("profit warning",     -0.80), ("earnings miss",      -0.80),
    ("below expectations", -0.70), ("missed estimates",   -0.75),
    ("downgrade",          -0.65), ("downgraded",         -0.65),
    ("underperform",       -0.60), ("underweight",        -0.50),
    ("dividend cut",       -0.80), ("dividend suspension",-0.75),
    ("default",            -0.75), ("debt restructuring", -0.60),
    ("fraud",              -0.90), ("scam",               -0.90),
    ("investigation",      -0.55), ("regulatory action",  -0.55),
    ("collapse",           -0.80), ("crash",              -0.75),
    ("layoffs",            -0.45), ("restructuring",      -0.35),
    ("loss widened",       -0.70), ("net loss",           -0.55),
    ("bearish",            -0.65), ("margin pressure",    -0.55),
    ("promoter selling",   -0.55), ("fii selling",        -0.50),
    ("52-week low",        -0.60), ("all-time low",       -0.70),
]

# News cache: { "SYMBOL:MARKET" -> (timestamp, result) }
_news_cache: dict[str, tuple[float, dict]] = {}
_news_lock = threading.Lock()
_NEWS_TTL = 10 * 60  # 10 minutes

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
        "https://www.moneycontrol.com/rss/buzzingstocks.xml",
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
        "https://www.moneycontrol.com/rss/marketreports.xml",
        "https://www.moneycontrol.com/rss/economy.xml",
    ],
}


def _fin_lexicon_score(text: str) -> float | None:
    """
    Check text against the financial lexicon phrases.
    Returns the compound score of the strongest match found, or None.
    Longer (more specific) phrases take priority over shorter ones.
    """
    t = text.lower()
    best: float | None = None
    best_len = 0
    for phrase, score in _FIN_LEXICON:
        if phrase in t and len(phrase) > best_len:
            best = score
            best_len = len(phrase)
    return best


def score_sentiment(title: str, description: str = "") -> dict:
    """
    Score sentiment using a two-layer approach:
    1. Financial lexicon override — catches domain terms VADER misses
    2. VADER — blended across title (70%) + description (30%)
    """
    # Layer 1: financial lexicon on combined text (title weighted more)
    combined = f"{title} {description}".strip()
    fin_score = _fin_lexicon_score(combined)

    # Layer 2: VADER scores
    vader_title = _vader.polarity_scores(title)["compound"]
    vader_desc  = _vader.polarity_scores(description)["compound"] if description else 0.0
    vader_blend = vader_title * 0.7 + vader_desc * 0.3

    # Blend: financial lexicon anchors if present, VADER modulates
    if fin_score is not None:
        # 60% lexicon, 40% VADER — lexicon is domain-authoritative
        compound = fin_score * 0.6 + vader_blend * 0.4
    else:
        compound = vader_blend

    compound = max(-1.0, min(1.0, compound))

    if compound >= 0.05:
        label = "BULLISH"
    elif compound <= -0.05:
        label = "BEARISH"
    else:
        label = "NEUTRAL"

    return {"label": label, "score": round(abs(compound), 3)}


class NewsSentimentService:
    async def get_news_with_sentiment(self, symbol: str, market: str, limit: int = 20) -> dict:
        cache_key = f"{symbol}:{market}"
        with _news_lock:
            cached = _news_cache.get(cache_key)
            if cached and (time.time() - cached[0]) < _NEWS_TTL:
                return cached[1]

        loop = asyncio.get_running_loop()
        articles = await loop.run_in_executor(None, self._fetch_rss, symbol, market, limit)
        # Presentation-only freshness annotation (Wave 0C display truthfulness):
        # per-article eligibility against the "general" stock-detail policy, so
        # the frontend can truthfully separate "current news used in sentiment"
        # from "historical context" WITHOUT duplicating the age policy in
        # TypeScript. Decision paths ignore these fields entirely —
        # _aggregate_sentiment performs its own horizon-specific split.
        now = datetime.now(timezone.utc)
        eligible_count = 0
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"], a.get("description", ""))
            eligible, reason = classify_article_freshness(a.get("published_at"), "general", now)
            a["sentiment_eligible"] = eligible
            a["eligibility_reason"] = reason
            eligible_count += 1 if eligible else 0
        # Wave 0D1: additive company-relevance annotation. Freshness meaning
        # (sentiment_eligible) is unchanged; company_sentiment_eligible is the
        # stricter fresh-AND-company_specific flag the UI groups by.
        classify_articles_relevance(articles, symbol, market)
        company_specific_count = sum(1 for a in articles if a.get("company_sentiment_eligible"))
        result = {
            "symbol": symbol, "market": market, "articles": articles,
            "total_article_count": len(articles),
            "eligible_article_count": eligible_count,
            "historical_article_count": len(articles) - eligible_count,
            "company_specific_article_count": company_specific_count,
            "contextual_article_count": eligible_count - company_specific_count,
        }

        with _news_lock:
            _news_cache[cache_key] = (time.time(), result)
        return result

    async def get_macro_news(self, market: str) -> dict:
        loop = asyncio.get_running_loop()
        articles = await loop.run_in_executor(None, self._fetch_macro_rss, market, 15)
        for a in articles:
            a["sentiment"] = score_sentiment(a["title"], a.get("description", ""))
        return {"market": market, "articles": articles}

    def _fetch_rss(self, symbol: str, market: str, limit: int) -> list:
        urls = [t.format(symbol=symbol) for t in RSS_FEEDS.get(market, RSS_FEEDS["US"])]
        sym_lower = symbol.lower()

        def _parse(url: str) -> list:
            try:
                feed = feedparser.parse(url)
                articles = []
                for entry in feed.entries[:limit * 2]:
                    title = entry.get("title", "")
                    is_broad_feed = "moneycontrol.com/rss/buzzing" in url
                    if is_broad_feed:
                        combined = (title + " " + entry.get("summary", "")).lower()
                        if sym_lower not in combined:
                            continue
                    articles.append({
                        "title":        title,
                        "source":       feed.feed.get("title", "RSS"),
                        "url":          entry.get("link", "#"),
                        "published_at": entry.get("published", ""),
                        "description":  entry.get("summary", "")[:300],
                    })
                return articles[:limit]
            except Exception as e:
                log.warning("RSS feed failed [%s]: %s", url, e)
                return []

        seen_urls: set[str] = set()
        articles: list = []
        with ThreadPoolExecutor(max_workers=len(urls)) as pool:
            for batch in pool.map(_parse, urls):
                for art in batch:
                    if art["url"] not in seen_urls:
                        seen_urls.add(art["url"])
                        articles.append(art)
                    if len(articles) >= limit:
                        break
        return articles[:limit]

    def _fetch_macro_rss(self, market: str, limit: int) -> list:
        seen_urls: set[str] = set()
        articles = []
        for url in MACRO_FEEDS.get(market, []):
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:limit]:
                    link = entry.get("link", "#")
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)
                    articles.append({
                        "title":        entry.get("title", ""),
                        "source":       feed.feed.get("title", "RSS"),
                        "url":          link,
                        "published_at": entry.get("published", ""),
                        "description":  entry.get("summary", "")[:300],
                    })
            except Exception as e:
                log.warning("Macro RSS feed failed [%s]: %s", url, e)
        return articles[:limit]
