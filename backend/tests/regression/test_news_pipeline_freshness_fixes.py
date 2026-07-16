"""
Product Integrity #019 — News & Sentiment pipeline freshness fixes.

Root cause (user-reported, DIXON): the Stock Detail page's News & Sentiment
section showed only 4-8 month old "historical context" articles and
"Insufficient fresh company-specific news evidence," despite genuine
same-day coverage existing. Investigation found four independent bugs:

  A. The Google News RSS query had no recency operator — Google ranks by
     relevance, not date, without one. Verified live: the un-filtered query
     for DIXON returned only months-old results; adding `when:14d` (matching
     SENTIMENT_MAX_AGE_DAYS["general"]) returned same-day articles.
  B. classify_article_relevance's company-name phrase match required the
     FULL run-on core-name phrase (e.g. "dixon technologies india" for
     "Dixon Technologies (India) Limited") — ordinary headlines like "Dixon
     Technologies shares rally 5%" never include the trailing country word,
     so they were classified `unknown` and excluded. _target_identifiers
     already computes a 2-word own_prefix ("dixon technologies") for peer
     EXCLUSION but it was discarded instead of also being accepted as a
     target match.
  C. The Economic Times per-symbol RSS URL (.../rssfeeds/{symbol}.cms) is
     dead — verified live it returns ET's generic "Most Viewed" homepage
     HTML, not RSS. ET's real per-stock feeds key on internal numeric IDs
     this codebase doesn't have a mapping for.
  D. The Yahoo Finance RSS feed (feeds.finance.yahoo.com/rss/2.0/headline)
     is deprecated — verified live (both US and IN query shapes) it returns
     Yahoo's generic HTML homepage, not RSS.

C and D silently contributed zero articles for EVERY symbol, not just
DIXON — removed rather than left in place. Ticker case-sensitivity in
_target_mentioned is deliberately untouched (see
test_word_boundaries_prevent_false_positive_matches in
test_news_relevance.py — "the tsm format is a file extension" must never
match ticker TSM; loosening that check would reintroduce exactly the
false-positive risk it exists to prevent).
"""
from services.news_sentiment import (
    _target_mentioned,
    _normalize_text,
    classify_article_relevance,
    RSS_FEEDS,
    MACRO_FEEDS,
)


def _mentioned(text: str, symbol: str, market: str) -> bool:
    return _target_mentioned(text, _normalize_text(text), symbol, market)


# ─── Bug B: 2-word company-name prefix now accepted as a target match ──────

def test_title_case_headline_with_full_two_word_name_now_matches():
    """The exact real-world DIXON case: ordinary title-case coverage
    without the ticker or the full "...India Limited" phrase."""
    assert _mentioned("Dixon Technologies shares rally 5% on strong Q1 earnings", "DIXON", "IN") is True
    assert _mentioned("Dixon Technologies Q4 results beat estimates, stock surges", "DIXON", "IN") is True


def test_all_caps_ticker_still_matches_unchanged():
    """Pre-existing behavior — the case-sensitive ticker channel — must
    still work exactly as before; this fix only adds a new path."""
    assert _mentioned("DIXON TECHNOLOGIES: Q1 results beat street estimates", "DIXON", "IN") is True


def test_full_three_word_phrase_still_matches_unchanged():
    """The original (now-secondary) full-phrase path must still work."""
    assert _mentioned("Dixon Technologies India Limited announces expansion", "DIXON", "IN") is True


def test_classify_article_relevance_now_reports_company_specific_for_dixon_headline():
    label, _reason = classify_article_relevance(
        "Dixon Technologies shares rally 5% on strong Q1 earnings", "", "DIXON", "IN"
    )
    assert label == "company_specific"


def test_ticker_case_sensitivity_is_unchanged_lowercase_prose_still_rejected():
    """Regression guard for the exact false-positive this design protects
    against — must not regress just because the phrase path got looser."""
    assert _mentioned("the tsm format is a file extension", "TSM", "US") is False


def test_single_word_company_names_are_still_not_matched_by_the_new_prefix_path():
    """own_prefixes requires >= 2 core words (existing _target_identifiers
    guarantee) — a single generic word must still not become a match path."""
    # A symbol whose core name is a single word never gets a 2-word
    # own_prefix at all (see _target_identifiers: `if len(words) >= 2`),
    # so this fix cannot introduce single-word matching for anyone.
    from services.news_sentiment import _target_identifiers
    _, own_prefixes = _target_identifiers("DIXON", "IN")
    assert all(len(p.split()) >= 2 for p in own_prefixes)


# ─── Bugs A/C/D: RSS feed configuration ────────────────────────────────────

def test_google_news_queries_include_recency_operator_matching_general_freshness_window():
    from services.news_sentiment import SENTIMENT_MAX_AGE_DAYS
    expected = f"when:{SENTIMENT_MAX_AGE_DAYS['general']}d"
    for market_feeds in (RSS_FEEDS["US"], RSS_FEEDS["IN"], MACRO_FEEDS["US"], MACRO_FEEDS["IN"]):
        google_urls = [u for u in market_feeds if "news.google.com" in u]
        assert google_urls, "expected at least one Google News URL per feed list"
        for url in google_urls:
            assert expected in url


def test_dead_yahoo_finance_rss_feed_removed_from_both_markets():
    for market_feeds in (RSS_FEEDS["US"], RSS_FEEDS["IN"], MACRO_FEEDS["US"], MACRO_FEEDS["IN"]):
        assert not any("feeds.finance.yahoo.com" in u for u in market_feeds)


def test_dead_et_symbol_based_feed_removed_but_working_numeric_id_macro_feed_kept():
    assert not any("rssfeeds/{symbol}.cms" in u for u in RSS_FEEDS["IN"])
    # The macro feed's numeric-ID ET URL is a genuinely different (working,
    # verified-live) endpoint — must NOT have been removed by this cleanup.
    assert any("economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms" in u for u in MACRO_FEEDS["IN"])


def test_every_remaining_feed_url_is_well_formed():
    for market_feeds in (RSS_FEEDS["US"], RSS_FEEDS["IN"], MACRO_FEEDS["US"], MACRO_FEEDS["IN"]):
        for url in market_feeds:
            assert url.startswith("https://")
