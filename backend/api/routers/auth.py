import logging
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Optional

from services.auth import get_current_user_id, require_matching_body_user, require_owner
from services.rate_limit import USER_DATA_RATE_LIMIT, limiter
from services.safe_errors import safe_error_message

log = logging.getLogger(__name__)

router = APIRouter()


class TermsAcceptance(BaseModel):
    user_id: str
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    mobile: Optional[str] = None
    country: Optional[str] = None
    terms_version: str = "v1.0"


@router.post("/api/auth/accept-terms")
@limiter.limit(USER_DATA_RATE_LIMIT)
async def accept_terms(body: TermsAcceptance, request: Request, current_user_id: str = Depends(get_current_user_id)):
    require_matching_body_user(body.user_id, current_user_id)
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
    ip = ip.split(",")[0].strip()

    USE_POSTGRES = __import__("os").getenv("USE_POSTGRES") == "1"
    if USE_POSTGRES:
        try:
            from services.postgres_store import _get_pool
            from datetime import datetime, timezone
            with _get_pool().connection() as conn:
                conn.execute(
                    """
                    INSERT INTO terms_acceptance
                        (user_id, email, first_name, last_name, mobile, country, ip_address, terms_version, accepted_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, terms_version) DO UPDATE
                        SET accepted_at = EXCLUDED.accepted_at,
                            ip_address  = EXCLUDED.ip_address,
                            first_name  = EXCLUDED.first_name,
                            last_name   = EXCLUDED.last_name,
                            mobile      = EXCLUDED.mobile,
                            country     = EXCLUDED.country
                    """,
                    (body.user_id, body.email, body.first_name, body.last_name,
                     body.mobile, body.country, ip, body.terms_version,
                     datetime.now(timezone.utc).isoformat()),
                )
            return {"status": "accepted", "terms_version": body.terms_version}
        except Exception as e:
            return {"status": "error", "detail": safe_error_message(
                log, "auth.accept_terms", e, "Unable to record acceptance right now. Please try again.")}

    return {"status": "accepted", "terms_version": body.terms_version}


@router.get("/api/auth/terms-status/{user_id}")
@limiter.limit(USER_DATA_RATE_LIMIT)
async def terms_status(request: Request, user_id: str, _owner: str = Depends(require_owner)):
    USE_POSTGRES = __import__("os").getenv("USE_POSTGRES") == "1"
    if USE_POSTGRES:
        try:
            from services.postgres_store import _get_pool
            with _get_pool().connection() as conn:
                row = conn.execute(
                    """SELECT terms_version, accepted_at, first_name, last_name, mobile, country
                       FROM terms_acceptance WHERE user_id = %s ORDER BY accepted_at DESC LIMIT 1""",
                    (user_id,)
                ).fetchone()
            if row:
                return {
                    "accepted": True,
                    "terms_version": row[0],
                    "accepted_at": str(row[1]),
                    "first_name": row[2],
                    "last_name": row[3],
                    "mobile": row[4],
                    "country": row[5],
                }
        except Exception:
            pass
    return {"accepted": False}
