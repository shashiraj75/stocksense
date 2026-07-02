"""
Wave 0D1 — deterministic company-relevance qualification for News Sentiment.

Root cause (Wave 0D-A audit, P0): fresh articles returned by ticker-based
search feeds were assumed company-relevant without verification — broad
"AI chip stocks" pieces, peer-company earnings, market commentary, and
loosely-linked profile articles all counted as direct company sentiment
(observed live for TSM), flowing into score, composite, confidence, Daily
Picks rank, badges, summaries, and reasoning.

All tests are deterministic fixtures with a fixed injected `now`. No RSS,
no providers, no network, no production calls.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.news_sentiment import (
    classify_article_relevance,
    classify_articles_relevance,
    _target_mentioned,
    _normalize_text,
)
from services.prediction_engine import PredictionEngine

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
ENGINE = PredictionEngine()


def _rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _article(title: str, label: str = "BULLISH", age: timedelta | None = timedelta(hours=2),
             description: str = "") -> dict:
    published = "" if age is None else _rfc2822(NOW - age)
    return {"title": title, "source": "test", "url": f"http://x/{abs(hash(title))}",
            "published_at": published, "description": description,
            "sentiment": {"label": label, "score": 0.7}}


# ─────────────────────────────────────────────────────────────────────────────
# A. Company identity and alias resolution
# ─────────────────────────────────────────────────────────────────────────────

def _mentioned(text: str, symbol: str, market: str) -> bool:
    return _target_mentioned(text, _normalize_text(text), symbol, market)


def test_sbin_matches_sbi_and_full_name():
    assert _mentioned("SBI posts record quarterly profit", "SBIN", "IN") is True
    assert _mentioned("State Bank of India raises deposit rates", "SBIN", "IN") is True
    assert _mentioned("SBIN shares gain after results", "SBIN", "IN") is True


def test_tcs_matches_full_company_name():
    assert _mentioned("Tata Consultancy Services wins large deal", "TCS", "IN") is True
    assert _mentioned("TCS announces buyback", "TCS", "IN") is True


def test_tsm_matches_all_registered_aliases():
    assert _mentioned("TSMC reports blowout quarter", "TSM", "US") is True
    assert _mentioned("Taiwan Semiconductor lifts guidance", "TSM", "US") is True
    assert _mentioned("Taiwan Semiconductor Manufacturing expands capacity", "TSM", "US") is True
    assert _mentioned("TSM stock climbs on earnings", "TSM", "US") is True


def test_word_boundaries_prevent_false_positive_matches():
    # Alias/ticker must never match inside unrelated longer words.
    assert _mentioned("Subsidiary results were mixed for the group", "SBIN", "IN") is False
    assert _mentioned("The catalystism of markets", "TSM", "US") is False
    # Lowercase prose must not trigger the case-sensitive ticker channel.
    assert _mentioned("the tsm format is a file extension", "TSM", "US") is False


def test_identity_resolution_is_shared_across_markets():
    import inspect
    from services.news_sentiment import classify_article_relevance as fn
    params = list(inspect.signature(fn).parameters)
    assert params == ["title", "description", "symbol", "market"]


# ─────────────────────────────────────────────────────────────────────────────
# B. Required TSM relevance fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _cls(title: str, description: str = "", symbol: str = "TSM", market: str = "US") -> str:
    return classify_article_relevance(title, description, symbol, market)[0]


def test_tsm_direct_earnings_article_is_company_specific():
    assert _cls("TSMC beats estimates as AI demand drives record quarter") == "company_specific"
    assert _cls("Taiwan Semiconductor raises full-year outlook") == "company_specific"


def test_tsm_collective_ai_chip_rally_is_not_company_specific():
    c = _cls("AI chip stocks rally; TSM among the biggest gainers")
    assert c in ("sector_relevant", "company_related_but_secondary")
    assert c != "company_specific"


def test_tsm_micron_earnings_article_is_not_company_specific():
    c = _cls("Micron Technology beats earnings estimates as TSM supply improves")
    assert c in ("peer_company", "company_related_but_secondary")


def test_tsm_intel_competitor_article_is_not_company_specific():
    # v1 peer detection channels: peer ticker token, two-word core-name
    # prefix, or registry alias. Single-word peer names without a ticker
    # ("Intel" alone) are a documented v1 boundary — headlines naming the
    # peer's ticker or two-word name are demoted.
    c = _cls("Intel (INTC) taps TSM foundry capacity for next-gen chips")
    assert c in ("peer_company", "company_related_but_secondary")


def test_tsm_broad_market_article_is_not_company_specific():
    c = _cls("Semiconductor stocks surge as Nasdaq hits record high")
    assert c in ("macro_market", "sector_relevant")
    c2 = _cls("Wall Street rallies on rate-cut hopes")
    assert c2 == "macro_market"


def test_tsm_investor_profile_article_is_not_company_specific():
    c = _cls("Nancy Pelosi's latest trades revealed: what she bought this month",
             description="Includes several technology names linked to AI, among them TSM.")
    assert c in ("unknown", "unrelated", "company_related_but_secondary")
    assert c != "company_specific"


def test_target_only_in_description_is_never_company_specific():
    c = _cls("Chipmakers prepare for the next upgrade cycle",
             description="Analysts expect Taiwan Semiconductor to benefit most.")
    assert c != "company_specific"


# ─────────────────────────────────────────────────────────────────────────────
# C. Aggregate and decision integrity
# ─────────────────────────────────────────────────────────────────────────────

def test_fully_company_specific_fresh_set_scores_identically_to_released_logic():
    arts = [
        _article("TSMC beats estimates on record AI demand", "BULLISH"),
        _article("Taiwan Semiconductor raises guidance", "BULLISH"),
        _article("TSM announces capacity expansion", "BULLISH"),
        _article("TSMC faces margin pressure from costs", "BEARISH"),
    ]
    result = ENGINE._aggregate_sentiment(arts, horizon="short", now=NOW, symbol="TSM", market="US")
    # Old formula on the same 4 qualified articles: 50 + (3-1)/4*50 = 75.
    assert result["score"] == 75
    assert result["label"] == "BULLISH"
    assert result["data_available"] is True
    assert result["company_specific_article_count"] == 4


def test_mixed_fresh_set_counts_only_company_specific_articles():
    arts = [
        _article("TSMC beats estimates on record AI demand", "BULLISH"),       # counts
        _article("AI chip stocks rally; TSM among gainers", "BULLISH"),        # context
        _article("Micron Technology beats earnings estimates", "BULLISH"),     # peer
        _article("Wall Street rallies on rate-cut hopes", "BULLISH"),          # macro
    ]
    result = ENGINE._aggregate_sentiment(arts, horizon="short", now=NOW, symbol="TSM", market="US")
    assert result["bullish"] == 1          # NOT 4 — irrelevant bullish cannot inflate
    assert result["bearish"] == 0
    assert result["company_specific_article_count"] == 1
    assert result["eligible_article_count"] == 4
    assert result["data_available"] is True


def test_fresh_irrelevant_bearish_articles_cannot_reduce_score():
    arts = [
        _article("TSMC beats estimates on record AI demand", "BULLISH"),
        _article("Micron Technology warns of weak demand", "BEARISH"),
        _article("Semiconductor stocks slide as Nasdaq drops", "BEARISH"),
    ]
    result = ENGINE._aggregate_sentiment(arts, horizon="short", now=NOW, symbol="TSM", market="US")
    assert result["bullish"] == 1 and result["bearish"] == 0
    assert result["score"] == 100  # untouched by irrelevant bearish noise


def test_contextual_only_fresh_set_is_unavailable_evidence():
    arts = [
        _article("AI chip stocks rally to record highs", "BULLISH"),
        _article("Micron Technology beats earnings estimates", "BULLISH"),
        _article("Nifty and Sensex close at all-time highs", "BULLISH"),
    ]
    result = ENGINE._aggregate_sentiment(arts, horizon="medium", now=NOW, symbol="TSM", market="US")
    assert result["data_available"] is False
    assert result["label"] == "NEUTRAL"
    assert result["bullish"] == 0 and result["bearish"] == 0
    assert result["freshness_status"] == "no_company_specific_news"
    assert result["reason_unavailable"] is not None
    assert result["company_specific_article_count"] == 0


def test_contextual_only_set_produces_no_badge_summary_or_ranking_evidence():
    """data_available=False flows through the already-released protections:
    no 📰 badge, no 'News sentiment is bullish' summary, ranking value None
    → z=0.0. Re-proven end-to-end via the Daily Picks row builder."""
    import services.daily_picks as dp

    contextual = ENGINE._aggregate_sentiment(
        [_article("AI chip stocks rally to record highs", "BULLISH")],
        horizon="long", now=NOW, symbol="TSM", market="US")

    class _FakeEngine:
        async def predict(self, symbol, market, horizon):
            return {
                "company_name": "TEST", "signal": "BUY", "current_price": 100.0,
                "target_price": 110.0, "confidence": 70, "trade_levels": {},
                "technical": {"score": 60}, "fundamental_score": {"score": 55},
                "sentiment_score": contextual, "quality_factors": {"score": 58},
                "reasoning": [], "score_band": None, "global_context": {},
                "market_regime": {}, "composite_score": 62.0, "confidence_score": 70,
            }

    with patch("services.daily_picks.PredictionEngine", return_value=_FakeEngine()), \
         patch("services.daily_picks.time.sleep"):
        row = dp._predict_stock("TSM", "long", "US")

    assert row["sentiment"] == "NEUTRAL"           # no Bullish News badge
    assert row["sentiment_score"] is None          # None → z = 0.0 in ranking
    assert "News sentiment is bullish" not in row["summary"]


def test_stale_direct_company_article_remains_unavailable():
    arts = [_article("TSMC beats estimates on record AI demand", "BULLISH", age=timedelta(days=300))]
    result = ENGINE._aggregate_sentiment(arts, horizon="long", now=NOW, symbol="TSM", market="US")
    assert result["data_available"] is False
    assert result["freshness_status"] == "no_fresh_news"  # freshness applies first, unchanged


def test_direct_article_never_excluded_by_presence_of_irrelevant_ones():
    arts = [
        _article("AI chip stocks rally; broad gains", "BULLISH"),
        _article("Taiwan Semiconductor lifts full-year guidance", "BULLISH"),
        _article("Nasdaq closes at record high", "BULLISH"),
    ]
    result = ENGINE._aggregate_sentiment(arts, horizon="short", now=NOW, symbol="TSM", market="US")
    assert result["data_available"] is True
    assert result["bullish"] == 1
    assert result["label"] == "BULLISH"


def test_no_symbol_backward_compatible_path_unchanged():
    """Without a target identity the aggregate stays freshness-only —
    exactly the Wave 0C contract, so all earlier callers/tests hold."""
    arts = [_article("AI chip stocks rally to record highs", "BULLISH")]
    result = ENGINE._aggregate_sentiment(arts, horizon="short", now=NOW)
    assert result["data_available"] is True
    assert result["bullish"] == 1


def test_india_and_us_share_identical_relevance_logic():
    # Same classifier, same precedence — India example mirrors the TSM cases.
    assert _cls("SBI posts record quarterly profit", symbol="SBIN", market="IN") == "company_specific"
    c = _cls("Bank stocks rally as RBI holds rates; SBI gains", symbol="SBIN", market="IN")
    assert c != "company_specific"
    c2 = _cls("Nifty and Sensex end at record highs", symbol="SBIN", market="IN")
    assert c2 == "macro_market"


# ─────────────────────────────────────────────────────────────────────────────
# D. Response annotation (route-level metadata)
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_articles_relevance_annotates_in_place():
    arts = [
        dict(_article("TSMC beats estimates on record AI demand", "BULLISH"), sentiment_eligible=True),
        dict(_article("AI chip stocks rally; TSM among gainers", "BULLISH"), sentiment_eligible=True),
        dict(_article("TSMC beats estimates (old)", "BULLISH"), sentiment_eligible=False),
    ]
    classify_articles_relevance(arts, "TSM", "US")
    assert arts[0]["relevance_class"] == "company_specific"
    assert arts[0]["company_sentiment_eligible"] is True
    assert arts[1]["company_sentiment_eligible"] is False   # fresh but contextual
    assert arts[2]["relevance_class"] == "company_specific"
    assert arts[2]["company_sentiment_eligible"] is False   # relevant but stale
    for a in arts:
        assert a["relevance_class"] in (
            "company_specific", "company_related_but_secondary", "peer_company",
            "sector_relevant", "macro_market", "unrelated", "unknown")
