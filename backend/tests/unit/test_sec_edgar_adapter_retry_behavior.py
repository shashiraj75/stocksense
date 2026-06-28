"""
Unit tests for SEC EDGAR Adapter retry/error/timeout behavior
(services/sec_edgar_adapter.py's `_get_with_retry`, SSDS-006 §4).

Closes the gap named explicitly in Epic 002 Sprint #004's report
("no dedicated unit test forces a 429/500/timeout response") — this
file is that test. No live network calls: `requests.get` is
monkeypatched to simulate each failure mode.
"""

import requests
import pytest

import services.sec_edgar_adapter as sea


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        return {}


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Retry backoff in production is deliberately slow (rate-limit-safe);
    tests shouldn't actually sleep for it — confirms the *logic* (attempt
    count, final outcome), not real-world timing."""
    monkeypatch.setattr(sea, "_RETRY_BACKOFF_SECONDS", 0.001)
    monkeypatch.setattr(sea, "_MIN_REQUEST_INTERVAL", 0.0)


@pytest.mark.unit
def test_429_triggers_retry_and_eventually_succeeds(monkeypatch):
    """A 429 (rate-limited) must be retried, not treated as a hard
    failure — confirms graceful degradation, not an immediate giveup."""
    calls = {"n": 0}

    def _fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(429)
        return _FakeResponse(200)

    monkeypatch.setattr(requests, "get", _fake_get)
    resp = sea._get_with_retry("https://example.com/test")
    assert resp.status_code == 200
    assert calls["n"] == 3


@pytest.mark.unit
def test_429_exhausts_retries_and_returns_none_not_an_exception():
    """A sustained 429 (the actual screener.in-style failure mode this
    engagement has already observed for a different provider) must
    degrade to None after exhausting the retry budget, never an
    uncaught exception — Fail-Soft Engineering (SSDS-006 §2). None
    (not a stale 429 response) is the correct "total failure" signal,
    consistent with how a sustained timeout degrades (see below)."""
    import services.sec_edgar_adapter as sea_module

    calls = {"n": 0}

    def _always_429(url, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(429)

    import requests as req
    orig = req.get
    req.get = _always_429
    try:
        resp = sea_module._get_with_retry("https://example.com/test")
    finally:
        req.get = orig

    assert resp is None
    assert calls["n"] == sea_module._RETRY_COUNT


@pytest.mark.unit
def test_500_triggers_retry_and_eventually_succeeds(monkeypatch):
    calls = {"n": 0}

    def _fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            return _FakeResponse(500)
        return _FakeResponse(200)

    monkeypatch.setattr(requests, "get", _fake_get)
    resp = sea._get_with_retry("https://example.com/test")
    assert resp.status_code == 200
    assert calls["n"] == 2


@pytest.mark.unit
def test_timeout_triggers_retry_and_then_gives_up_gracefully(monkeypatch):
    """A sustained timeout (requests.exceptions.Timeout, a subclass of
    RequestException) must be caught, retried up to the bound, and then
    return None — never propagate as an uncaught exception up to the
    adapter's public entry point."""
    calls = {"n": 0}

    def _always_times_out(url, headers=None, timeout=None):
        calls["n"] += 1
        raise requests.exceptions.Timeout("simulated timeout")

    monkeypatch.setattr(requests, "get", _always_times_out)
    resp = sea._get_with_retry("https://example.com/test")
    assert resp is None
    assert calls["n"] == sea._RETRY_COUNT


@pytest.mark.unit
def test_timeout_then_recovery_still_succeeds(monkeypatch):
    calls = {"n": 0}

    def _fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout("simulated timeout")
        return _FakeResponse(200)

    monkeypatch.setattr(requests, "get", _fake_get)
    resp = sea._get_with_retry("https://example.com/test")
    assert resp is not None
    assert resp.status_code == 200


@pytest.mark.unit
def test_404_is_not_retried():
    """A 404 (confirmed-unavailable resource) is a definite failure, not
    a transient one — SES-002 §6's 'distinguish temporarily unavailable
    from a bug' rule. Retrying it would waste rate-limit budget on
    something retrying can never fix."""
    calls = {"n": 0}

    def _fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(404)

    import requests as req
    orig = req.get
    req.get = _fake_get
    try:
        resp = sea._get_with_retry("https://example.com/test")
    finally:
        req.get = orig

    assert resp.status_code == 404
    assert calls["n"] == 1  # confirms no retry happened


@pytest.mark.unit
def test_unexpected_status_is_not_retried():
    """A 403 (e.g. a credential/permission problem) is also a definite
    failure, not transient — same reasoning as the 404 case above, for
    a different non-retryable status family."""
    calls = {"n": 0}

    def _fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(403)

    import requests as req
    orig = req.get
    req.get = _fake_get
    try:
        resp = sea._get_with_retry("https://example.com/test")
    finally:
        req.get = orig

    assert resp.status_code == 403
    assert calls["n"] == 1


@pytest.mark.unit
def test_fetch_company_facts_degrades_gracefully_on_total_failure(monkeypatch):
    """End-to-end confirmation: when the HTTP layer can't get a usable
    response at all, fetch_company_facts must return None — never raise
    — so a refresh job calling this for many symbols isn't taken down
    by one provider outage (the existing per-symbol isolation pattern
    this codebase already proves elsewhere, e.g. fundamentals_refresh.py)."""
    monkeypatch.setattr(sea, "_get_with_retry", lambda url: None)
    sea._facts_cache = {}
    result = sea.fetch_company_facts(999999999)
    assert result is None


@pytest.mark.unit
def test_fetch_us_fundamentals_sec_edgar_degrades_gracefully_when_facts_unavailable(monkeypatch):
    """The adapter's own public entry point must surface a clean
    available=False, not an exception, when the underlying HTTP layer
    is fully degraded — confirms the failure mode is graceful all the
    way up to the function any future caller would actually use."""
    monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
    monkeypatch.setattr(sea, "fetch_company_facts", lambda cik: None)
    result = sea.fetch_us_fundamentals_sec_edgar("AAPL")
    assert result["available"] is False
    assert "reason" in result
