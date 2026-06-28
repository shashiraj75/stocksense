"""
Security Closure Sprint, Task 2 — rate limiter client-IP detection.

Root cause this locks in: `services/rate_limit.py` originally used
slowapi's default `get_remote_address`, which reads `request.client.host`
— the immediate TCP peer, which on Railway is the edge proxy's own
address, not the real client's. This meant the intended "60 requests per
minute per user" limit instead behaved as "60 requests per minute combined
across every user sharing the same apparent proxy IP" — confirmed as a
real risk by this exact codebase's own prior precedent (`auth.py`'s
pre-existing `accept_terms` route already needed `X-Forwarded-For` for
correct IP logging in this same deployment, for the identical reason).

These tests exercise `get_client_ip` directly with hand-built ASGI scopes
(no live Railway request needed) covering every scenario named in the
Security Closure Sprint brief.
"""

import pytest
from starlette.requests import Request

from services.rate_limit import get_client_ip


def _request(headers: dict | None = None, client_host: str | None = "10.0.0.5") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 12345) if client_host else None,
    }
    return Request(scope)


@pytest.mark.regression
class TestRateLimiterClientIp:
    def test_local_request_no_forwarded_header(self):
        """Outside Railway (local dev, no reverse proxy): falls back to
        request.client.host, unchanged from slowapi's original default
        behavior."""
        req = _request(headers={}, client_host="127.0.0.1")
        assert get_client_ip(req) == "127.0.0.1"

    def test_railway_forwarded_request_single_hop(self):
        """A request that passed through exactly one reverse-proxy hop
        (the expected Railway shape): X-Forwarded-For holds the one real
        client IP Railway's edge proxy observed."""
        req = _request(headers={"x-forwarded-for": "203.0.113.7"})
        assert get_client_ip(req) == "203.0.113.7"

    def test_multiple_clients_distinguished(self):
        """Two different real users behind Railway must resolve to two
        different keys — this is the actual defect being fixed: before
        this fix, both would have collapsed to the same
        request.client.host (Railway's proxy address)."""
        req_a = _request(headers={"x-forwarded-for": "203.0.113.7"})
        req_b = _request(headers={"x-forwarded-for": "198.51.100.23"})
        assert get_client_ip(req_a) != get_client_ip(req_b)
        assert get_client_ip(req_a) == "203.0.113.7"
        assert get_client_ip(req_b) == "198.51.100.23"

    def test_malformed_forwarded_header_falls_back(self):
        """Empty / comma-only X-Forwarded-For values must not crash or
        resolve to an empty string — falls back to client.host."""
        req = _request(headers={"x-forwarded-for": "   ,  ,"}, client_host="10.0.0.9")
        assert get_client_ip(req) == "10.0.0.9"

    def test_missing_forwarded_header_and_no_client(self):
        """Neither X-Forwarded-For nor a client tuple at all — the final,
        documented fallback ("127.0.0.1"), never a crash."""
        req = _request(headers={}, client_host=None)
        assert get_client_ip(req) == "127.0.0.1"

    def test_spoofed_prefix_with_real_railway_appended_ip_trusts_rightmost(self):
        """The exact spoofing attempt Railway's own support thread
        addresses: a client manually sets X-Forwarded-For to a fake IP
        before the request reaches Railway's proxy. Railway's proxy
        APPENDS the real connecting IP to the end of the existing value
        rather than replacing it — so the rightmost entry (not the
        attacker-controlled leftmost one) must be what's trusted."""
        req = _request(headers={"x-forwarded-for": "6.6.6.6, 203.0.113.7"})
        assert get_client_ip(req) == "203.0.113.7"

    def test_pure_client_spoofing_attempt_alone_not_trusted_over_real_chain(self):
        """A longer forged chain (simulating an attacker prepending
        several fake hops) still resolves to the one real, rightmost
        entry — confirms the fix doesn't just handle the two-element case."""
        req = _request(headers={"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3, 203.0.113.7"})
        assert get_client_ip(req) == "203.0.113.7"
