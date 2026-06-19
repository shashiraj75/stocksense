from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class TermsAcceptance(BaseModel):
    user_id: str
    email: str
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
                    INSERT INTO terms_acceptance (user_id, email, ip_address, terms_version, accepted_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, terms_version) DO UPDATE
                        SET accepted_at = EXCLUDED.accepted_at,
                            ip_address  = EXCLUDED.ip_address
                    """,
                    (body.user_id, body.email, ip, body.terms_version,
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
                    "SELECT terms_version, accepted_at FROM terms_acceptance WHERE user_id = %s ORDER BY accepted_at DESC LIMIT 1",
                    (user_id,)
                ).fetchone()
            if row:
                return {"accepted": True, "terms_version": row[0], "accepted_at": str(row[1])}
        except Exception:
            pass
    return {"accepted": False}
