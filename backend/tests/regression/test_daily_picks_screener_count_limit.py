"""
Product Integrity Workstream #002A — regression test locking in the fix
for `_get_universe_by_mcap`'s `yf.screen()` call.

Root cause this addresses: Yahoo's screen() endpoint now hard-rejects
`count > 250` ("Yahoo limits query count to 250, reduce count") — confirmed
by direct, live reproduction against the exact query this function issues.
Before the fix, every call requested `count=1000`, always raised, was
silently caught, and fell back to the full, unfiltered static universe on
every single Daily Picks run (for both markets) — the market-cap
pre-filter never actually narrowed anything. This test locks in the
corrected, Yahoo-accepted count and confirms a successful screener result
is still used (not the fallback) when the screener call succeeds.

Updated in Product Integrity Workstream #002D-B: `_get_universe_by_mcap`
now returns a (symbols, universe_used, universe_degraded) 3-tuple.
For US: screener symbols are intersected with _US_DAILY_PICKS_HEURISTIC_FILTERED
before being returned; the raw 12k universe is never returned for US.
Tests updated to destructure the tuple and verify the new behavior.
"""

from unittest.mock import MagicMock, patch

from services.daily_picks import _get_universe_by_mcap, _US_DAILY_PICKS_HEURISTIC_FILTERED_SET


def test_screen_is_called_with_yahoo_accepted_page_size():
    """Release 12B: IN pages the screener with size <= 250 (Yahoo's max for
    custom queries; `count` only applies to predefined queries and produced
    the 25-row truncation). #002A's "never exceed 250 per request" rule holds."""
    fake_result = {"quotes": [{"symbol": "RELIANCE.NS"}, {"symbol": "TCS.NS"}]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result) as mock_screen:
        syms, universe_used, universe_degraded, _ = _get_universe_by_mcap("IN")
    for call in mock_screen.call_args_list:
        assert call.kwargs["size"] <= 250
        assert "count" not in call.kwargs or call.kwargs["count"] is None
    # 2 symbols < healthy minimum → truthful degraded static fallback.
    assert universe_used == "static_fallback"
    assert universe_degraded is True


def test_screen_count_value_error_falls_back_to_static_fallback():
    """Release 12B: IN screener failure must not crash and must fall back to
    the bounded curated static list, truthfully flagged as degraded (the
    unbounded full-NSE fallback is retired)."""
    with patch("services.daily_picks.yf.screen", side_effect=ValueError("Yahoo limits query count to 250, reduce count")):
        syms, universe_used, universe_degraded, _ = _get_universe_by_mcap("IN")
    assert isinstance(syms, list)
    assert len(syms) > 0
    assert universe_used == "static_fallback"
    assert universe_degraded is True


def test_us_screen_also_uses_yahoo_accepted_count():
    """
    yf.screen() must be called with count <= 250 for US (#002A regression).
    After #002D-B: result is intersected with _US_DAILY_PICKS_HEURISTIC_FILTERED — AAPL
    and MSFT are valid common-equity tickers and must survive the intersection.
    """
    fake_result = {"quotes": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result) as mock_screen:
        syms, universe_used, universe_degraded, _ = _get_universe_by_mcap("US")
    assert mock_screen.call_args.kwargs["count"] <= 250
    # Both AAPL and MSFT are in _US_DAILY_PICKS_HEURISTIC_FILTERED and must be present
    assert "AAPL" in syms
    assert "MSFT" in syms
    assert universe_used == "screener"
    assert universe_degraded is False
