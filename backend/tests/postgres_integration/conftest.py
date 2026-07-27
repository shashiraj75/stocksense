"""
Real PostgreSQL Verification, CI Execution phase — shared fixtures for the
`postgres_integration` test suite (Part 3).

Every test here requires `POSTGRES_INTEGRATION_TEST_DATABASE_URL` to be
explicitly set — deliberately a DIFFERENT name from `DATABASE_URL` (the
variable the application itself reads), so this suite can never
accidentally pick up whatever `DATABASE_URL` happens to already be set to
in a developer's shell or a misconfigured job. Once validated, this
mirrors the value into `DATABASE_URL` so the application's own connection
pools (`services.postgres_store`, `api.routers.paper_trading`) pick it up
exactly as they would in any real deployment — no test-only code path is
introduced into the application itself, and `_conn` is NEVER mocked
anywhere in this directory.

Production-isolation guard: the host must be `localhost`/`127.0.0.1` (the
CI service container always is), and is independently checked against a
list of known production-provider host substrings. A legitimate remote
disposable/staging database (e.g. a Supabase development branch) is an
approved environment per the governing program, but is NOT auto-accepted
here — pointing this suite at one requires a human to deliberately review
and run it locally with that URL, not an unattended default.
"""
import os
import time
import uuid
from urllib.parse import urlparse

import jwt
import psycopg
import pytest

_PRODUCTION_HOST_SUBSTRINGS = (
    "supabase.co", "supabase.com", "railway.app", "amazonaws.com", "rds.amazonaws.com",
)

SYNTHETIC_USER_PREFIX = "pg-integration-test-user-"

TEST_JWT_SECRET = "postgres-integration-test-secret-at-least-32-bytes-long"
TEST_SUPABASE_URL = "https://postgres-integration-test.supabase.co"


def _validate_not_production(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        pytest.fail("postgres_integration: could not parse a host from POSTGRES_INTEGRATION_TEST_DATABASE_URL")
    for bad in _PRODUCTION_HOST_SUBSTRINGS:
        if bad in host:
            pytest.fail(
                f"postgres_integration: refusing to run — host {host!r} matches a known "
                f"production-provider pattern ({bad!r}). This suite only targets a disposable "
                "local/CI PostgreSQL instance."
            )
    if host not in ("localhost", "127.0.0.1"):
        pytest.fail(
            f"postgres_integration: refusing to run — host {host!r} is not localhost/127.0.0.1. "
            "This suite only auto-runs against the ephemeral CI service container; a remote "
            "host (even a legitimate staging one) requires a human to review before use."
        )


@pytest.fixture(scope="session")
def pg_database_url() -> str:
    url = os.environ.get("POSTGRES_INTEGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "POSTGRES_INTEGRATION_TEST_DATABASE_URL not set — postgres_integration tests "
            "require an explicit, disposable PostgreSQL instance and never fall back to "
            "any other configuration (never production Supabase)."
        )
    _validate_not_production(url)
    os.environ["DATABASE_URL"] = url
    return url


@pytest.fixture(scope="session")
def pg_admin_conn(pg_database_url):
    with psycopg.connect(pg_database_url, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def initialized_schema(pg_admin_conn):
    """Runs the repository's REAL initialization function once per session
    — never a hand-copied simplified schema (Part 4)."""
    from services import postgres_store
    postgres_store.init_db()
    return True


@pytest.fixture(scope="session", autouse=True)
def _disable_rate_limiting_for_this_suite():
    """The real Buy rate limit (60/min per IP, see services/rate_limit.py)
    would otherwise interact with this suite's own concurrency tests
    (≥10 simultaneous requests from one TestClient, effectively one IP) in
    a way that has nothing to do with what this suite is verifying —
    PostgreSQL transaction/idempotency behavior, not rate-limit
    interaction. Disabled only within this test session; the application
    code and its default-enabled rate limiting are otherwise untouched."""
    from services.rate_limit import limiter
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


@pytest.fixture
def pg_conn(pg_database_url, initialized_schema):
    with psycopg.connect(pg_database_url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def unique_user_id() -> str:
    return f"{SYNTHETIC_USER_PREFIX}{uuid.uuid4().hex[:16]}"


@pytest.fixture(autouse=True)
def _cleanup_synthetic_rows(pg_database_url, initialized_schema):
    """Every row this suite creates belongs to a user_id under
    SYNTHETIC_USER_PREFIX — deleted unconditionally after each test
    (pass or fail) so the suite is always safe to rerun and never
    accumulates cross-test state."""
    yield
    with psycopg.connect(pg_database_url, autocommit=True) as conn:
        with conn.transaction():
            conn.execute(
                "DELETE FROM paper_trade_entry_snapshot WHERE user_id LIKE %s",
                (f"{SYNTHETIC_USER_PREFIX}%",)
            )
            conn.execute(
                "DELETE FROM paper_trade_idempotency_key WHERE user_id LIKE %s",
                (f"{SYNTHETIC_USER_PREFIX}%",)
            )
            conn.execute(
                "DELETE FROM paper_trades WHERE user_id LIKE %s",
                (f"{SYNTHETIC_USER_PREFIX}%",)
            )
            conn.execute(
                "DELETE FROM paper_portfolio WHERE user_id LIKE %s",
                (f"{SYNTHETIC_USER_PREFIX}%",)
            )


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture(autouse=True)
def _force_markets_open(monkeypatch):
    """Real market-hours gating is a real-clock/calendar check unrelated
    to what this suite verifies (Part 18: avoid unrelated external
    dependencies) — always-open here, exactly like every other paper
    trading test file in this repository."""
    monkeypatch.setattr("api.routers.paper_trading._is_market_open", lambda market: True)


def make_auth_header(user_id: str) -> dict:
    token = jwt.encode(
        {"sub": user_id, "aud": "authenticated", "iss": f"{TEST_SUPABASE_URL}/auth/v1", "exp": time.time() + 3600},
        TEST_JWT_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(pg_database_url, initialized_schema):
    """A real TestClient wired to the REAL ephemeral PostgreSQL — `_conn`
    is never mocked anywhere in this directory; every request exercises
    the actual connection pool and SQL the production application uses."""
    from api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def ensure_portfolio(pg_conn, user_id: str, cash: float = 1_000_000.0, cash_usd: float = 100_000.0) -> None:
    pg_conn.execute(
        """INSERT INTO paper_portfolio (session_id, user_id, cash, cash_usd)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE SET cash = %s, cash_usd = %s""",
        (user_id, user_id, cash, cash_usd, cash, cash_usd)
    )


def get_portfolio_cash(pg_conn, user_id: str, market: str = "US") -> float:
    col = "cash_usd" if market == "US" else "cash"
    row = pg_conn.execute(f"SELECT {col} FROM paper_portfolio WHERE user_id = %s", (user_id,)).fetchone()
    return row[0] if row else 0.0
