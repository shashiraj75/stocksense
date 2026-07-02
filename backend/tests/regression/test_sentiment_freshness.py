"""
UI/UX Truthfulness Correction Program — Wave 0C: fresh-news eligibility,
stale-news exclusion, and truthful sentiment reasoning.

Root cause: articles' published_at was captured as a raw string and never
parsed — months-old articles carried full weight in the sentiment score,
composite score, confidence factor agreement, Daily Picks ranking factor,
the "Bullish News" badge, the generated summary, and reasoning text that
falsely said "in recent news."

All tests are deterministic: fixture articles with controlled ages against
a fixed injected `now`. No RSS/provider/network calls anywhere.
"""

from datetime import datetime, timedelta, timezone

from services.news_sentiment import (
    SENTIMENT_MAX_AGE_DAYS,
    is_article_eligible,
    parse_published_at,
    split_articles_by_freshness,
)
from services.prediction_engine import PredictionEngine

# Fixed deterministic "now" for every test in this file.
NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


def _rfc2822(dt: datetime) -> str:
    """Format like real RSS feeds do (RFC-2822)."""
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _article(age: timedelta | None, label: str = "BULLISH", title: str = "t") -> dict:
    published = "" if age is None else _rfc2822(NOW - age)
    return {"title": title, "source": "test", "url": f"http://x/{title}",
            "published_at": published, "sentiment": {"label": label, "score": 0.7}}


ENGINE = PredictionEngine()


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_published_at_handles_rfc2822_and_iso():
    assert parse_published_at("Wed, 01 Jul 2026 09:30:00 +0000") == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    assert parse_published_at("2026-07-01T09:30:00+00:00") == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    # Naive ISO assumed UTC
    assert parse_published_at("2026-07-01T09:30:00") == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)


def test_parse_published_at_never_raises_and_never_invents_dates():
    for bad in ["", None, "not a date", "0", "yesterday", "32 Foo 2026", 12345]:
        assert parse_published_at(bad) is None, f"input={bad!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 1-4. Horizon-specific eligibility policy
# ─────────────────────────────────────────────────────────────────────────────

def test_same_day_article_eligible_for_all_horizons():
    raw = _rfc2822(NOW - timedelta(hours=3))
    for horizon in ("short", "medium", "long", None):
        assert is_article_eligible(raw, horizon, NOW) is True, f"horizon={horizon}"


def test_four_day_article_ineligible_short_eligible_medium_long_general():
    raw = _rfc2822(NOW - timedelta(days=4))
    assert is_article_eligible(raw, "short", NOW) is False
    assert is_article_eligible(raw, "medium", NOW) is True
    assert is_article_eligible(raw, "long", NOW) is True
    assert is_article_eligible(raw, None, NOW) is True  # general = 14d


def test_fifteen_day_article_eligible_only_for_long():
    raw = _rfc2822(NOW - timedelta(days=15))
    assert is_article_eligible(raw, "short", NOW) is False
    assert is_article_eligible(raw, "medium", NOW) is False
    assert is_article_eligible(raw, None, NOW) is False
    assert is_article_eligible(raw, "long", NOW) is True  # long = 30d


def test_thirty_one_day_article_ineligible_everywhere():
    raw = _rfc2822(NOW - timedelta(days=31))
    for horizon in ("short", "medium", "long", None):
        assert is_article_eligible(raw, horizon, NOW) is False, f"horizon={horizon}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Eleven-month-old bullish article contributes nothing anywhere
# ─────────────────────────────────────────────────────────────────────────────

def test_eleven_month_old_bullish_article_has_zero_decision_weight():
    stale = [_article(timedelta(days=335), "BULLISH")]
    result = ENGINE._aggregate_sentiment(stale, horizon="medium", now=NOW)
    assert result["data_available"] is False       # → excluded from composite,
    assert result["label"] == "NEUTRAL"            #   confidence factor agreement,
    assert result["score"] == 50                   #   and never a badge source
    assert result["bullish"] == 0                  # not counted as bullish evidence
    assert result["freshness_status"] == "no_fresh_news"
    assert result["historical_article_count"] == 1
    assert result["eligible_article_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Mixed set — only eligible articles contribute
# ─────────────────────────────────────────────────────────────────────────────

def test_mixed_set_counts_only_eligible_articles():
    articles = [
        _article(timedelta(hours=5), "BULLISH", "fresh1"),
        _article(timedelta(days=1), "BEARISH", "fresh2"),
        _article(timedelta(days=200), "BULLISH", "stale1"),   # must not count
        _article(timedelta(days=300), "BULLISH", "stale2"),   # must not count
        _article(None, "BULLISH", "undated"),                 # must not count
    ]
    result = ENGINE._aggregate_sentiment(articles, horizon="medium", now=NOW)
    assert result["data_available"] is True
    assert result["bullish"] == 1
    assert result["bearish"] == 1
    assert result["score"] == 50  # 1v1 among eligible — NOT 4v1 among all
    assert result["total_article_count"] == 5
    assert result["eligible_article_count"] == 2
    assert result["historical_article_count"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# 7. All articles stale → unavailable, no fabricated conclusion
# ─────────────────────────────────────────────────────────────────────────────

def test_all_stale_articles_report_unavailable_not_bullish():
    stale = [_article(timedelta(days=150 + i * 30), "BULLISH", f"s{i}") for i in range(9)]
    result = ENGINE._aggregate_sentiment(stale, horizon="short", now=NOW)
    # The exact SBI scenario: 9 old bullish articles previously scored 100.
    assert result["data_available"] is False
    assert result["label"] == "NEUTRAL"
    assert result["bullish"] == 0 and result["bearish"] == 0
    assert result["reason_unavailable"] is not None


def test_unavailable_sentiment_triggers_existing_redistribution_contract():
    """data_available=False is the exact flag the composite's no-news
    redistribution path and the confidence engine's factor-agreement
    exclusion already key on — locking in that stale-only input produces it."""
    stale = [_article(timedelta(days=100), "BULLISH")]
    result = ENGINE._aggregate_sentiment(stale, horizon="long", now=NOW)
    assert result["data_available"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 8-9. Malformed and future dates
# ─────────────────────────────────────────────────────────────────────────────

def test_malformed_and_missing_dates_are_ineligible_without_exceptions():
    articles = [
        {"title": "a", "url": "u1", "published_at": "", "sentiment": {"label": "BULLISH"}},
        {"title": "b", "url": "u2", "published_at": "garbage text", "sentiment": {"label": "BULLISH"}},
        {"title": "c", "url": "u3", "sentiment": {"label": "BULLISH"}},  # key absent
    ]
    result = ENGINE._aggregate_sentiment(articles, horizon="medium", now=NOW)
    assert result["data_available"] is False
    assert result["eligible_article_count"] == 0


def test_future_dated_articles_beyond_tolerance_are_ineligible():
    slightly_future = _rfc2822(NOW + timedelta(hours=2))   # clock skew — tolerated
    far_future = _rfc2822(NOW + timedelta(days=3))         # nonsense — ineligible
    assert is_article_eligible(slightly_future, "medium", NOW) is True
    assert is_article_eligible(far_future, "medium", NOW) is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. Identical eligible sets score identically to the old formula
# ─────────────────────────────────────────────────────────────────────────────

def test_scoring_unchanged_for_fully_fresh_article_set():
    fresh = [
        _article(timedelta(hours=1), "BULLISH"),
        _article(timedelta(hours=2), "BULLISH"),
        _article(timedelta(hours=3), "BULLISH"),
        _article(timedelta(hours=4), "BEARISH"),
    ]
    result = ENGINE._aggregate_sentiment(fresh, horizon="short", now=NOW)
    # Old formula: 50 + (3-1)/4*50 = 75 — must be byte-identical.
    assert result["score"] == 75
    assert result["label"] == "BULLISH"
    assert result["data_available"] is True
    assert result["freshness_status"] == "fresh"


def test_empty_article_list_still_reports_unavailable():
    result = ENGINE._aggregate_sentiment([], horizon="medium", now=NOW)
    assert result["data_available"] is False
    assert result["freshness_status"] == "no_articles"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Daily Picks badge / generated summary cannot come from stale news
# ─────────────────────────────────────────────────────────────────────────────

def test_daily_picks_badge_and_summary_gate_on_unavailable_sentiment():
    """The Daily Picks card badge uses sentiment_score.label and the
    generated summary adds 'News sentiment is bullish.' when label==BULLISH
    or score>=60. Stale-only input now yields NEUTRAL/50/unavailable, so
    neither can fire — while fresh input still behaves normally."""
    from services.daily_picks import _build_summary

    stale_sent = ENGINE._aggregate_sentiment(
        [_article(timedelta(days=200), "BULLISH")], horizon="long", now=NOW)
    fresh_sent = ENGINE._aggregate_sentiment(
        [_article(timedelta(days=1), "BULLISH")], horizon="long", now=NOW)

    def summary_for(sent):
        return _build_summary({
            "company_name": "TEST", "symbol": "TEST", "confidence": 70,
            "current_price": 100.0, "target_price": 110.0,
            "technical": {"score": 65}, "fundamental_score": {"score": 60},
            "sentiment_score": sent, "market_regime": {}, "global_context": {},
            "quality_factors": {},
        }, "long")

    assert "News sentiment is bullish" not in summary_for(stale_sent)
    assert stale_sent["label"] == "NEUTRAL"   # → no 📰 badge on the card
    assert "News sentiment is bullish" in summary_for(fresh_sent)
    assert fresh_sent["label"] == "BULLISH"


# ─────────────────────────────────────────────────────────────────────────────
# 12. India and US parity
# ─────────────────────────────────────────────────────────────────────────────

def test_freshness_logic_is_market_agnostic():
    """Eligibility is a pure function of published_at + horizon + now — the
    helper takes no market/symbol/currency argument at all, so IN and US
    articles with identical timestamps behave identically by construction."""
    raw = _rfc2822(NOW - timedelta(days=10))
    import inspect
    params = set(inspect.signature(is_article_eligible).parameters)
    assert params == {"published_at_raw", "horizon", "now"}
    assert is_article_eligible(raw, "medium", NOW) is True


# ─────────────────────────────────────────────────────────────────────────────
# Reasoning text truthfulness (source-level lock)
# ─────────────────────────────────────────────────────────────────────────────

def test_reasoning_no_longer_claims_unverified_recent_news():
    import inspect
    import services.prediction_engine as pe
    src = inspect.getsource(pe)
    assert "in eligible recent news" in src
    assert "bearish in recent news" not in src, (
        "The reasoning text must not claim bare 'recent news' — recency is "
        "now verified, and the wording must say so."
    )


def test_policy_constants_are_centralized():
    assert SENTIMENT_MAX_AGE_DAYS == {"short": 3, "medium": 14, "long": 30, "general": 14}
