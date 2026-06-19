from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

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
async def accept_terms(body: TermsAcceptance, request: Request):
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
            return {"status": "error", "detail": str(e)}

    return {"status": "accepted", "terms_version": body.terms_version}


@router.get("/api/auth/terms-status/{user_id}")
async def terms_status(user_id: str):
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
