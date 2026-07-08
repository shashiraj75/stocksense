"""
Sprint 011 — one-time-per-process DDL guard (Portfolio audit finding 3(e),
folded into the Performance-Scalability-UX-Sprint-011 spec's Phase 1 scope).

portfolio.py and alerts.py used to re-issue CREATE TABLE IF NOT EXISTS /
CREATE INDEX IF NOT EXISTS / ALTER TABLE ... on every request before the
real query — three-plus no-op DDL round-trips per read. The guard runs the
idempotent DDL once per process and must:
  1. execute the full DDL on the first call;
  2. execute NOTHING on subsequent calls;
  3. stay unset after a failure so the next request retries (preserving the
     lazy-create behavior when the DB only comes up after startup).

No DB: _conn is monkeypatched with a recording fake.
"""
import pytest

from api.routers import alerts, portfolio


class _FakeConn:
    """Context-manager stand-in for a psycopg connection that records SQL."""

    def __init__(self, statements):
        self._statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._statements.append(" ".join(sql.split()))


def _guard_contract(module, table_name, monkeypatch):
    """The three guarantees, identical for every router using this pattern."""
    monkeypatch.setattr(module, "_schema_ready", False)

    # (3) failure leaves the guard unset so the next request retries
    def _db_down():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(module, "_conn", _db_down)
    with pytest.raises(RuntimeError):
        module._ensure_table()
    assert module._schema_ready is False

    # (1) first successful call runs the full idempotent DDL
    statements = []
    monkeypatch.setattr(module, "_conn", lambda: _FakeConn(statements))
    module._ensure_table()
    ddl_count = len(statements)
    assert ddl_count >= 3  # CREATE TABLE + CREATE INDEX + ALTER TABLE at minimum
    assert any(table_name in s for s in statements)
    assert module._schema_ready is True

    # (2) every later call is a no-op — zero further round-trips
    module._ensure_table()
    module._ensure_table()
    assert len(statements) == ddl_count


@pytest.mark.unit
class TestPortfolioSchemaGuard:
    def test_ddl_runs_once_and_failure_retries(self, monkeypatch):
        _guard_contract(portfolio, "portfolio_holdings", monkeypatch)


@pytest.mark.unit
class TestAlertsSchemaGuard:
    def test_ddl_runs_once_and_failure_retries(self, monkeypatch):
        _guard_contract(alerts, "price_alerts", monkeypatch)
