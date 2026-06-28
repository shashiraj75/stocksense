"""
Unit tests for SEC EDGAR Adapter ticker→CIK resolution
(services/sec_edgar_adapter.py, SSDS-006 Sprint #004).

No live network calls — `_fetch_ticker_map` is monkeypatched per SES-003
§1's "still no live network calls" rule for this category. The CIKs used
below (AAPL=320193, JPM=19617) were confirmed live against SEC's own
company_tickers.json during this sprint, not invented.
"""

import time

import pytest

import services.sec_edgar_adapter as sea


@pytest.fixture(autouse=True)
def _reset_ticker_map_cache():
    """The ticker-map cache is module-level global state — reset it before
    and after every test so tests don't leak into each other, per SES-003
    §3's rule for any test touching global/process-level state."""
    sea._ticker_map_cache = None
    yield
    sea._ticker_map_cache = None


@pytest.mark.unit
def test_resolve_cik_returns_known_symbol(monkeypatch):
    monkeypatch.setattr(sea, "_fetch_ticker_map", lambda: {"AAPL": 320193, "JPM": 19617})
    assert sea.resolve_cik("AAPL") == 320193
    assert sea.resolve_cik("jpm") == 19617  # case-insensitive, mirrors screener_data.py's .upper() convention


@pytest.mark.unit
def test_resolve_cik_returns_none_for_unknown_symbol(monkeypatch):
    """Never guesses a CIK for an unresolvable symbol — UNAVAILABLE, not fabricated."""
    monkeypatch.setattr(sea, "_fetch_ticker_map", lambda: {"AAPL": 320193})
    assert sea.resolve_cik("NOTAREALTICKERXYZ") is None


@pytest.mark.unit
def test_resolve_cik_uses_cache_within_ttl(monkeypatch):
    """A second resolve_cik call within the TTL window must not re-fetch
    the ticker map — confirms the 24h cache (Section 4's rate-limit-safe
    behavior) is actually exercised, not just declared."""
    call_count = {"n": 0}

    def _fake_fetch():
        call_count["n"] += 1
        return {"AAPL": 320193}

    monkeypatch.setattr(sea, "_fetch_ticker_map", _fake_fetch)

    sea.resolve_cik("AAPL")
    sea.resolve_cik("AAPL")
    sea.resolve_cik("AAPL")

    assert call_count["n"] == 1


@pytest.mark.unit
def test_resolve_cik_refetches_after_ttl_expires(monkeypatch):
    call_count = {"n": 0}

    def _fake_fetch():
        call_count["n"] += 1
        return {"AAPL": 320193}

    monkeypatch.setattr(sea, "_fetch_ticker_map", _fake_fetch)
    monkeypatch.setattr(sea, "_TICKER_MAP_TTL", 0)  # expire immediately

    sea.resolve_cik("AAPL")
    time.sleep(0.01)
    sea.resolve_cik("AAPL")

    assert call_count["n"] == 2


@pytest.mark.unit
def test_fetch_ticker_map_falls_back_when_download_fails(monkeypatch):
    """Fail-Soft Engineering (SSDS-006 §2): a failed live download degrades
    to the evidence-based fallback map, never raises, never returns an
    empty/unusable map."""
    monkeypatch.setattr(sea, "_get_with_retry", lambda url: None)
    mapping = sea._fetch_ticker_map()
    assert mapping == sea._FALLBACK_CIK_MAP


@pytest.mark.unit
def test_fetch_ticker_map_falls_back_on_malformed_json(monkeypatch):
    class _BadResponse:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(sea, "_get_with_retry", lambda url: _BadResponse())
    mapping = sea._fetch_ticker_map()
    assert mapping == sea._FALLBACK_CIK_MAP


@pytest.mark.unit
def test_fetch_ticker_map_parses_real_shaped_response(monkeypatch):
    """Confirms the parser handles SEC's actual response shape — a dict
    of dicts, each with cik_str/ticker/title — exactly as returned by a
    live call to company_tickers.json during this sprint."""
    class _GoodResponse:
        status_code = 200

        def json(self):
            return {
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
            }

    monkeypatch.setattr(sea, "_get_with_retry", lambda url: _GoodResponse())
    mapping = sea._fetch_ticker_map()
    assert mapping == {"AAPL": 320193, "MSFT": 789019}
