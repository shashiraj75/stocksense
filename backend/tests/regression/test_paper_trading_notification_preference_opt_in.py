"""
Paper Trading notification preference — opt-in default correction
(2026-09-14).

Triggered by a real user report: many distinct accounts were receiving
frequent "near target"/"near stop loss" proximity emails they never
explicitly asked for, and separately, the on-screen proximity alert
wasn't appearing for days even when the email did arrive.

Root causes found by direct code reading:
1. `paper_portfolio.email_notifications_enabled` defaulted to `true` in
   the schema — every user was silently opted in, never given an explicit
   choice.
2. The frontend's ONLY on-screen channel for a proximity alert was a
   native browser Notification() popup, gated behind BOTH the email
   preference AND the browser's own notification permission — which most
   users never explicitly grant, so the on-screen alert effectively never
   appeared for them even when their email preference was on.

Fixes:
- Schema default flipped to `false` (opt-in), with a one-time backfill
  for existing rows guarded by a new `email_notifications_touched_at`
  column so a user who later explicitly re-enables it is never silently
  reset on a future restart.
- PATCH /api/paper-trading/notifications now stamps
  `email_notifications_touched_at`.
- Frontend: an always-on in-page banner (not gated by the email
  preference or browser permission) is now the primary on-screen channel;
  the native browser popup remains an opt-in bonus layer only.

These tests cover the backend pieces (schema SQL text, since this
statement runs as part of a idempotent startup script with no isolated
unit boundary — mirroring this codebase's own established convention for
schema-init changes — and the PATCH endpoint's touched_at stamping via
the existing _conn-mocking pattern from test_paper_trading_authorization.py).
The frontend wiring is covered by
frontend/src/app/paper-trading/__tests__/notificationChannels.test.ts.
"""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_STORE_SOURCE = (Path(__file__).parents[2] / "services" / "postgres_store.py").read_text()


# ── 1. Schema SQL text ──────────────────────────────────────────────────────

def test_email_notifications_enabled_add_column_default_is_false():
    assert (
        "ALTER TABLE paper_portfolio ADD COLUMN IF NOT EXISTS "
        "email_notifications_enabled BOOLEAN NOT NULL DEFAULT false;"
    ) in _STORE_SOURCE


def test_email_notifications_enabled_has_an_explicit_set_default_false():
    """ADD COLUMN IF NOT EXISTS's own DEFAULT clause is a no-op for a
    column that already exists in a live database — an explicit ALTER
    COLUMN ... SET DEFAULT is required for the corrected default to
    actually take effect there."""
    assert (
        "ALTER TABLE paper_portfolio ALTER COLUMN email_notifications_enabled "
        "SET DEFAULT false;"
    ) in _STORE_SOURCE


def test_email_notifications_touched_at_column_exists():
    assert "email_notifications_touched_at TIMESTAMPTZ" in _STORE_SOURCE


def test_backfill_update_is_guarded_by_touched_at_is_null():
    """The one-time backfill must never re-flip a row whose owner has since
    explicitly chosen a value (on or off) — only a row that has never been
    touched at all."""
    assert (
        "UPDATE paper_portfolio SET email_notifications_enabled = false\n"
        "    WHERE email_notifications_enabled = true AND email_notifications_touched_at IS NULL;"
    ) in _STORE_SOURCE


# ── 2. PATCH /notifications stamps touched_at ───────────────────────────────

class _RecordingConn:
    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextmanager
def _fake_conn(fetchone_results=None):
    yield _RecordingConn(fetchone_results=fetchone_results)


def test_patch_notifications_stamps_touched_at():
    import time
    import jwt
    from fastapi.testclient import TestClient

    secret = "regression-test-jwt-secret-at-least-32-bytes-long"
    issuer = "https://test-project.supabase.co/auth/v1"
    token = jwt.encode(
        {"sub": "user-notif-1", "aud": "authenticated", "iss": issuer, "exp": time.time() + 3600},
        secret, algorithm="HS256",
    )

    with patch.dict("os.environ", {
        "SUPABASE_JWT_SECRET": secret,
        "SUPABASE_URL": "https://test-project.supabase.co",
    }):
        from api.main import app
        client = TestClient(app)

        # _ensure_portfolio's own SELECT needs a 3-column row (cash, cash_usd,
        # email_notifications_enabled) so it doesn't think the portfolio is
        # missing and attempt an INSERT first.
        conn = _RecordingConn(fetchone_results=[(100000.0, 100000.0, False)])

        @contextmanager
        def _conn_factory():
            yield conn

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory
        ):
            resp = client.patch(
                "/api/paper-trading/notifications",
                json={"email_notifications_enabled": True},
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    update_calls = [c for c in conn.calls if "UPDATE paper_portfolio SET email_notifications_enabled" in c[0]]
    assert len(update_calls) == 1
    sql, params = update_calls[0]
    assert "email_notifications_touched_at = now()" in sql
    assert params[0] is True
