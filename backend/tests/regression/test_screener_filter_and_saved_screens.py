"""
Screener multi-criteria filter + saved screens (2026-09-15).

Replaces api/routers/screener.py's previous `/filter` implementation — a
live yf.Ticker(...).info loop across the entire market universe on every
request, confirmed via direct frontend code search to never have been
wired to any UI, and with a dead `signal` param accepted but never
applied. The new implementation filters the same weekly-refreshed
`stock_fundamentals_cache` table query_screen's fixed Multibagger screens
already use — instant, no live scraping — and adds per-user saved filter
sets (Postgres-primary/JSON-file-fallback, mirroring
api/routers/watchlist.py's own proven shape).

Two things get tested:
1. fundamentals_cache.query_filtered's SQL-building logic (a mocked
   connection records the exact SQL/params executed) — every filterable
   column comes from a fixed whitelist, `sector` is the only free-text
   value and must always be bound as a parameter, never concatenated.
2. The saved-screens endpoints' ownership enforcement, mirroring
   test_paper_trading_authorization.py's JWT + _conn-mocking pattern —
   a user can never read/write another user's saved screens.
"""
import time
from contextlib import contextmanager
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "regression-test-jwt-secret-at-least-32-bytes-long"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _token(sub: str = "user-aaa", exp_delta: float = 3600) -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + exp_delta},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


# ── 1. query_filtered's SQL-building logic ──────────────────────────────────

class _RecordingConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_query_filtered_uses_the_correct_market_cap_column_per_market():
    from services import fundamentals_cache as cache

    conn = _RecordingConn()
    with patch.object(cache, "_conn", return_value=conn):
        cache.query_filtered(market="IN", min_market_cap=500)
    sql, params = conn.calls[0]
    # The SELECT list always lists every column (including
    # market_cap_usd_m, for display) — the WHERE-clause fragment is what
    # must differ by market.
    assert "market_cap_cr IS NOT NULL AND market_cap_cr >= %s" in sql
    assert "market_cap_usd_m IS NOT NULL AND market_cap_usd_m >= %s" not in sql
    assert 500 in params

    conn2 = _RecordingConn()
    with patch.object(cache, "_conn", return_value=conn2):
        cache.query_filtered(market="US", min_market_cap=500)
    sql2, _ = conn2.calls[0]
    assert "market_cap_usd_m IS NOT NULL AND market_cap_usd_m >= %s" in sql2


def test_query_filtered_binds_sector_as_a_parameter_never_concatenated():
    from services import fundamentals_cache as cache

    conn = _RecordingConn()
    malicious = "Tech'; DROP TABLE stock_fundamentals_cache; --"
    with patch.object(cache, "_conn", return_value=conn):
        cache.query_filtered(market="IN", sector=malicious)
    sql, params = conn.calls[0]
    # The raw sector value must never appear inside the SQL text itself —
    # only as a bound parameter.
    assert malicious not in sql
    assert any(malicious in str(p) for p in params)
    assert "sector_name ILIKE %s" in sql


def test_query_filtered_only_applies_clauses_for_provided_filters():
    from services import fundamentals_cache as cache

    conn = _RecordingConn()
    with patch.object(cache, "_conn", return_value=conn):
        cache.query_filtered(market="IN")
    sql, params = conn.calls[0]
    # No optional filter supplied -> only the market clause + LIMIT param
    # (the SELECT list always lists every column for display — checking
    # the WHERE clause specifically, not the SQL text as a whole).
    assert "sector_name ILIKE" not in sql
    assert "pe_ratio IS NOT NULL" not in sql
    assert params == ["IN", 200]


def test_query_filtered_combines_multiple_criteria_with_and():
    from services import fundamentals_cache as cache

    conn = _RecordingConn()
    with patch.object(cache, "_conn", return_value=conn):
        cache.query_filtered(
            market="US", max_pe=25, min_roe=15, min_business_quality_score=70,
        )
    sql, params = conn.calls[0]
    assert "pe_ratio IS NOT NULL AND pe_ratio <= %s" in sql
    assert "roe_pct IS NOT NULL AND roe_pct >= %s" in sql
    assert "business_quality_score IS NOT NULL AND business_quality_score >= %s" in sql
    assert params == ["US", 25, 15, 70, 200]


# ── 2. Saved screens ownership enforcement ──────────────────────────────────

class _FakeSavedScreensConn:
    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.calls = []
        self._fetchall_result = fetchall_result or []
        self._fetchone_result = fetchone_result

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def connection(self):
        yield self._conn


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/screener/saved/user-aaa", None),
    ("post", "/api/screener/saved/user-aaa",
     {"name": "My Screen", "market": "IN", "filters": {}}),
    ("delete", "/api/screener/saved/user-aaa/1", None),
])
class TestSavedScreensAuthorization:
    def test_missing_token_rejected(self, client, method, path, body):
        resp = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert resp.status_code in (401, 403)

    def test_cross_user_forbidden(self, client, method, path, body):
        # Authenticated as user-bbb, but the path names user-aaa.
        resp = getattr(client, method)(path, json=body, headers=_auth("user-bbb")) \
            if body is not None else getattr(client, method)(path, headers=_auth("user-bbb"))
        assert resp.status_code == 403


def test_owner_can_read_their_own_saved_screens(client):
    # _USE_PG is a module-level constant read once at import time (same
    # established pattern as watchlist.py's own _USE_PG) — patching the
    # env var alone doesn't affect an already-imported module, so the
    # attribute itself must be patched to exercise the Postgres path.
    from api.routers import screener as screener_router
    conn = _FakeSavedScreensConn(fetchall_result=[(1, "My Screen", "IN", {"min_roe": 15}, None)])
    with patch.object(screener_router, "_USE_PG", True), \
         patch("services.postgres_store._get_pool", return_value=_FakePool(conn)):
        resp = client.get("/api/screener/saved/user-aaa", headers=_auth("user-aaa"))
    assert resp.status_code == 200
    assert resp.json()["screens"][0]["name"] == "My Screen"


def test_owner_can_save_a_screen(client):
    from api.routers import screener as screener_router
    conn = _FakeSavedScreensConn(fetchone_result=(7,))
    with patch.object(screener_router, "_USE_PG", True), \
         patch("services.postgres_store._get_pool", return_value=_FakePool(conn)):
        resp = client.post(
            "/api/screener/saved/user-aaa",
            json={"name": "Quality Compounders", "market": "IN", "filters": {"min_business_quality_score": 70}},
            headers=_auth("user-aaa"),
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == 7
    insert_sql = conn.calls[0][0]
    assert "INSERT INTO saved_screens" in insert_sql
