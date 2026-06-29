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
"""

from unittest.mock import MagicMock, patch

from services.daily_picks import _get_universe_by_mcap


def test_screen_is_called_with_yahoo_accepted_count():
    fake_result = {"quotes": [{"symbol": "RELIANCE.NS"}, {"symbol": "TCS.NS"}]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result) as mock_screen:
        syms = _get_universe_by_mcap("IN")
    assert mock_screen.call_args.kwargs["count"] <= 250
    assert syms == ["RELIANCE", "TCS"]


def test_screen_count_value_error_falls_back_to_full_universe():
    with patch("services.daily_picks.yf.screen", side_effect=ValueError("Yahoo limits query count to 250, reduce count")):
        syms = _get_universe_by_mcap("IN")
    # Falls back, but does not crash — confirms the existing safety net
    # still works even if Yahoo's own limit changes again in the future.
    assert isinstance(syms, list)
    assert len(syms) > 0


def test_us_screen_also_uses_yahoo_accepted_count():
    fake_result = {"quotes": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]}
    with patch("services.daily_picks.yf.screen", return_value=fake_result) as mock_screen:
        syms = _get_universe_by_mcap("US")
    assert mock_screen.call_args.kwargs["count"] <= 250
    assert syms == ["AAPL", "MSFT"]
