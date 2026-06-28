"""
Basic per-IP rate limiting for the user-data endpoints fixed in Security
Remediation Sprint #001 (H-2 in the Mini Security Audit: no rate limiting
existed anywhere in the API, which directly compounded C-1's IDOR risk by
leaving nothing to slow down user_id enumeration/brute-forcing attempts).

Narrowly scoped: only the four routers touched by this sprint (portfolio,
watchlist, alerts, auth's terms endpoints) use this limiter. No other
router, engine, or provider is affected.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Generous enough not to disrupt a real user's normal usage (multiple tabs,
# fast successive holding edits), tight enough to block naive enumeration.
USER_DATA_RATE_LIMIT = "60/minute"
