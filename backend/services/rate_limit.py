"""
Basic per-IP rate limiting for the user-data endpoints fixed in Security
Remediation Sprint #001 (H-2 in the Mini Security Audit: no rate limiting
existed anywhere in the API, which directly compounded C-1's IDOR risk by
leaving nothing to slow down user_id enumeration/brute-forcing attempts).

Narrowly scoped: only the four routers touched by this sprint (portfolio,
watchlist, alerts, auth's terms endpoints) use this limiter. No other
router, engine, or provider is affected.

Security Closure Sprint — Task 2 (client-IP detection behind Railway):
the Production Validation Sprint found this limiter likely bucketed every
user together. slowapi's default `get_remote_address` reads
`request.client.host`, which on Railway is the edge proxy's own address,
not the real client's — confirmed by this exact codebase's own prior
precedent: `api/routers/auth.py`'s pre-existing `accept_terms` route
already had to fall back to `X-Forwarded-For` for correct IP logging,
for the identical reason, before this sprint ever existed.

Trust model, stated explicitly (documenting the assumption, not just
making it): Railway's edge proxy is treated as the single trusted
reverse-proxy hop in front of this backend. A reverse proxy APPENDS the
IP of whichever peer it actually received the connection from to the END
of any `X-Forwarded-For` value already present on the request — so the
RIGHTMOST entry is the one Railway's own proxy wrote from the real TCP
connection, while anything to its left could have been supplied by the
client itself and is not trusted for this purpose. This matches Railway's
own documented resolution to exactly this concern (a user manually
setting X-Forwarded-For to spoof their IP): Railway support's answer is
"the rightmost value... is trustworthy" — a different, more specific
recommendation than their separate "take the first IP" guidance, which
addresses cross-routing-path *consistency*, not spoofing resistance.

Residual, named assumption: this reasoning holds only because Railway's
network topology puts its own proxy as the sole hop between the public
internet and this service — if the service were ever reachable through
some other path that bypassed Railway's proxy entirely, a client hitting
it directly could still forge `X-Forwarded-For` and have its rightmost
entry trusted. Railway's managed routing does not expose such a bypass
today; if that topology ever changes, this assumption should be
re-verified, not silently carried forward.

Outside Railway (local development, `pytest`, or any deployment with no
reverse proxy in front): `X-Forwarded-For` is simply absent from real
requests in that situation, so this falls through to the same
`request.client.host` behavior as before this fix — unaffected, no
behavior change for local dev.
"""

from slowapi import Limiter
from starlette.requests import Request


def get_client_ip(request: Request) -> str:
    """
    Real per-client IP for rate-limiting purposes. See module docstring
    for the trust model and its one named assumption. Falls back to
    `request.client.host` (slowapi's original default behavior) when
    `X-Forwarded-For` is absent — i.e., unchanged behavior for local
    development and any non-proxied deployment.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        candidates = [part.strip() for part in xff.split(",") if part.strip()]
        if candidates:
            return candidates[-1]  # rightmost = Railway's own proxy-observed peer

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


limiter = Limiter(key_func=get_client_ip)

# Generous enough not to disrupt a real user's normal usage (multiple tabs,
# fast successive holding edits), tight enough to block naive enumeration.
USER_DATA_RATE_LIMIT = "60/minute"
