"""
Learning Alpha Engine remediation, Phase 1 — paper-trade provenance
foundation (corrected per reviewer feedback on the first implementation
pass).

Regression proofs:
  1. A new trade opened WITH provenance fields stores them verbatim.
  2. A new trade opened WITHOUT provenance fields stores NULL for every one
     of them — never guessed, never backfilled from anywhere else.
  3. execution_slippage_pct is computed ONLY when BOTH
     recommendation_source is known AND recommendation_reference_price is a
     finite, strictly-positive number — malformed (zero/negative)
     reference prices, or a reference price with no recommendation_source,
     leave it NULL, not a garbage/undefined ratio.
  4. signal_override is ALWAYS NULL in this phase — never derived from the
     client-supplied `signal` field, which is not an authoritative
     recommendation record. Regardless of what combination of
     signal/recommendation_source is supplied, it stays NULL.
  5. levels_modified_after_entry is set TRUE only when stop_loss or
     target_price genuinely changes from its stored value. An
     entry_price-only correction, or a no-op edit resubmitting the same
     stop_loss/target_price, must NOT set it. Once TRUE, a later no-op edit
     must not revert it to FALSE.
  6. Existing (pre-remediation-shaped) reads are unaffected by the new
     nullable columns — GET /portfolio's older-closed-trades read path,
     which uses the untouched _TRADE_ROW_COLUMNS/_trade_row_to_dict shape,
     still works against a plain 15-column row.
"""

import math
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


def _token(sub: str = "user-aaa") -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


class _RecordingConn:
    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        """Trade Postmortem Engine, Stage 2 — paper_buy now wraps the trade
        + entry-snapshot INSERTs in `with conn.transaction():`. No-op here
        (this fake has no real rollback semantics); the parametrized
        provenance assertions below only inspect recorded SQL/params, which
        are unaffected by the transaction wrapper."""
        yield self


@contextmanager
def _fake_conn(fetchone_results=None):
    yield _RecordingConn(fetchone_results=fetchone_results)


def _buy_conn_factory():
    """Two _conn() calls per /buy: _ensure_portfolio's SELECT, then the
    debit UPDATE + INSERT inside paper_buy's own `with` block."""
    pending = [
        _RecordingConn(fetchone_results=[(1_000_000.0, 100_000.0, True)]),
        _RecordingConn(fetchone_results=[(999_900.0,), (1,)]),
    ]
    made = []

    def factory():
        conn = pending.pop(0)
        made.append(conn)
        return conn

    return factory, made


def _insert_call(made_conns):
    """Find the INSERT INTO paper_trades call among all recorded calls."""
    for conn in made_conns:
        for sql, params in conn.calls:
            if "INSERT INTO paper_trades" in sql:
                return sql, params
    raise AssertionError("no INSERT INTO paper_trades call was recorded")


def _buy(client, body):
    factory, made = _buy_conn_factory()
    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory
    ), patch("api.routers.paper_trading._is_market_open", return_value=True):
        resp = client.post("/api/paper-trading/buy", json=body, headers=_auth())
    return resp, made


@pytest.mark.regression
class TestPaperTradingProvenance:
    def test_new_trade_with_provenance_supplied_is_stored(self, client):
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
            "signal": "BUY", "horizon": "medium",
            "recommendation_source": "daily_pick",
            "daily_pick_run_id": "run-123",
            "daily_pick_rank": 2,
            "recommendation_generated_at": "2026-07-13T00:00:00Z",
            "recommendation_reference_price": 100.0,
            "recommendation_entry_low": 98.0,
            "recommendation_entry_high": 102.0,
            "recommendation_original_stop_loss": 90.0,
            "recommendation_original_target": 120.0,
            "model_version": "v1",
        }
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        assert "daily_pick" in params
        assert "run-123" in params
        assert 2 in params
        assert 98.0 in params
        assert 102.0 in params
        assert 90.0 in params
        assert 120.0 in params
        assert "v1" in params

    def test_new_trade_without_provenance_defaults_to_null(self, client):
        body = {"symbol": "MSFT", "market": "US", "quantity": 1, "price": 50.0}
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        # Last 12 bound params are the new provenance columns in INSERT
        # column order; every one must be None when nothing was supplied.
        new_provenance_params = params[-12:]
        assert all(p is None for p in new_provenance_params), new_provenance_params

    # ── execution_slippage_pct ───────────────────────────────────────────────

    def test_execution_slippage_computed_when_source_and_reference_price_both_present(self, client):
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 110.0,
            "recommendation_source": "daily_pick",
            "recommendation_reference_price": 100.0,
        }
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        assert params[-2] == pytest.approx(10.0)

    def test_execution_slippage_null_without_reference_price(self, client):
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 110.0,
            "recommendation_source": "daily_pick",
        }
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        assert params[-2] is None

    def test_execution_slippage_null_without_recommendation_source_even_with_reference_price(self, client):
        """A reference price alone is not enough — recommendation_source
        must also be known, since an unattributed reference price isn't a
        real recommendation record."""
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 110.0,
            "recommendation_reference_price": 100.0,
        }
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        assert params[-2] is None

    @pytest.mark.parametrize("bad_price", [0.0, -50.0])
    def test_execution_slippage_null_for_non_positive_reference_price(self, client, bad_price):
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 110.0,
            "recommendation_source": "daily_pick",
            "recommendation_reference_price": bad_price,
        }
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        assert params[-2] is None

    def test_execution_slippage_handles_division_safely_no_crash(self, client):
        """Regression guard: a reference price of exactly 0 must never reach
        the division — proven above it stays NULL — and the request must
        succeed (no 500) rather than raise ZeroDivisionError."""
        body = {
            "symbol": "AAPL", "market": "US", "quantity": 1, "price": 110.0,
            "recommendation_source": "daily_pick",
            "recommendation_reference_price": 0.0,
        }
        resp, _ = _buy(client, body)
        assert resp.status_code == 200

    # ── signal_override — always NULL in this phase ─────────────────────────

    @pytest.mark.parametrize("body_extra", [
        {"signal": "HOLD", "recommendation_source": "daily_pick"},
        {"signal": "SELL", "recommendation_source": "daily_pick"},
        {"signal": "BUY", "recommendation_source": "daily_pick"},
        {"signal": "HOLD"},
        {},
    ])
    def test_signal_override_always_null_regardless_of_inputs(self, client, body_extra):
        body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 110.0, **body_extra}
        resp, made = _buy(client, body)
        assert resp.status_code == 200
        _, params = _insert_call(made)
        assert params[-1] is None

    # ── levels_modified_after_entry ──────────────────────────────────────────

    def _edit(self, client, recorder, body):
        @contextmanager
        def _recording_fake_conn():
            yield recorder

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _recording_fake_conn
        ):
            return client.patch("/api/paper-trading/trade/1", json=body, headers=_auth())

    def test_levels_modified_after_entry_set_when_stop_loss_genuinely_changes(self, client):
        # SELECT shape: user_id, status, entry_price, quantity, market, stop_loss, target_price
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp = self._edit(client, recorder, {"stop_loss": 95.0, "target_price": 120.0})
        assert resp.status_code == 200
        update_calls = [c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0]]
        assert len(update_calls) == 1
        sql, params = update_calls[0]
        assert "levels_modified_after_entry = TRUE" in sql
        assert 95.0 in params and 120.0 in params

    def test_levels_modified_after_entry_set_when_target_price_genuinely_changes(self, client):
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp = self._edit(client, recorder, {"stop_loss": 90.0, "target_price": 150.0})
        assert resp.status_code == 200
        sql, params = next(c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry = TRUE" in sql

    def test_levels_modified_after_entry_not_set_on_noop_edit(self, client):
        """Resubmitting the exact same stop_loss/target_price must not set
        the flag."""
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp = self._edit(client, recorder, {"stop_loss": 90.0, "target_price": 120.0})
        assert resp.status_code == 200
        sql, params = next(c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry" not in sql

    def test_levels_modified_after_entry_not_set_on_entry_price_only_correction(self, client):
        """An entry_price-only correction — the client resubmits the SAME
        stop_loss/target_price it already has stored, and only entry_price
        differs — must not set the flag."""
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp = self._edit(client, recorder, {
            "stop_loss": 90.0, "target_price": 120.0, "entry_price": 105.0,
        })
        assert resp.status_code == 200
        sql, params = next(c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry" not in sql

    def test_entry_price_only_request_with_omitted_levels_preserves_stored_stop_loss_and_target(self, client):
        """The real-world entry-price-only case: the request body genuinely
        OMITS stop_loss/target_price (not resubmits their existing values —
        EditRequest defaults both to None, so this is a different code path
        than the "resubmitted" test above). Must preserve the stored
        90.0/120.0, never wipe them to NULL, and must not set
        levels_modified_after_entry."""
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp = self._edit(client, recorder, {"entry_price": 105.0})
        assert resp.status_code == 200
        sql, params = next(c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry" not in sql
        # The stored levels must be preserved verbatim, not nulled out.
        assert 90.0 in params and 120.0 in params
        assert None not in params

    def test_explicit_null_stop_loss_still_clears_it_and_counts_as_a_genuine_change(self, client):
        """A client that explicitly sends {"stop_loss": null, ...} — not
        omitting the key — intentionally clears that level. This is
        distinct from omission (tested above) and remains supported; a
        stored 90.0 -> explicit None is a genuine change, so
        levels_modified_after_entry IS set."""
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp = self._edit(client, recorder, {"stop_loss": None, "target_price": 120.0})
        assert resp.status_code == 200
        sql, params = next(c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry = TRUE" in sql
        assert None in params and 120.0 in params

    def test_entry_price_only_omission_does_not_touch_target_price_either(self, client):
        """Same as the stop_loss case, but confirms target_price alone is
        also preserved (not just stop_loss) when both are omitted."""
        recorder = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 250.0, 2, "IN", 200.0, 300.0)])
        resp = self._edit(client, recorder, {"entry_price": 260.0})
        assert resp.status_code == 200
        sql, params = next(c for c in recorder.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry" not in sql
        assert 200.0 in params and 300.0 in params

    def test_levels_modified_after_entry_never_reverts_to_false(self, client):
        """Once a genuine change has set the flag TRUE, a later no-op edit
        must never write FALSE — the code only ever omits the column from
        the UPDATE (leaving whatever value is already stored) or sets it
        TRUE; it never sets FALSE anywhere."""
        # First edit: genuine stop_loss change (stored levels are now
        # effectively TRUE from the DB's point of view — simulated here by
        # feeding the SECOND edit's SELECT the already-changed values).
        recorder1 = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 90.0, 120.0)])
        resp1 = self._edit(client, recorder1, {"stop_loss": 95.0, "target_price": 120.0})
        assert resp1.status_code == 200
        sql1, _ = next(c for c in recorder1.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry = TRUE" in sql1

        # Second edit: no-op relative to the now-current (95.0, 120.0)
        # stored values — must not touch the flag at all, so whatever TRUE
        # value the first edit set survives untouched.
        recorder2 = _RecordingConn(fetchone_results=[("user-aaa", "OPEN", 100.0, 1, "US", 95.0, 120.0)])
        resp2 = self._edit(client, recorder2, {"stop_loss": 95.0, "target_price": 120.0})
        assert resp2.status_code == 200
        sql2, _ = next(c for c in recorder2.calls if "UPDATE paper_trades SET stop_loss" in c[0])
        assert "levels_modified_after_entry" not in sql2

    def test_no_code_path_ever_writes_levels_modified_after_entry_false(self):
        """Static guard: the source never contains a literal
        `levels_modified_after_entry = FALSE` — the column is only ever
        set TRUE or left untouched, guaranteeing monotonicity by
        construction rather than by test coverage alone."""
        import pathlib
        import api.routers.paper_trading as pt
        source = pathlib.Path(pt.__file__).read_text()
        assert "levels_modified_after_entry = FALSE" not in source
        assert "levels_modified_after_entry=FALSE" not in source.replace(" ", "")

    def test_existing_older_closed_trades_read_path_unaffected(self, client):
        """GET .../closed-trades/older reads via the pre-remediation,
        untouched _TRADE_ROW_COLUMNS (15 columns) — must still work
        unchanged; the new nullable paper_trades columns are additive-only
        and never appear in this SELECT."""
        import datetime as _dt

        row = (
            1, "AAPL", "US", 1, 100.0, 110.0, "CLOSED", "BUY", "medium",
            _dt.datetime(2026, 6, 1, tzinfo=_dt.timezone.utc),
            _dt.datetime(2026, 6, 5, tzinfo=_dt.timezone.utc),
            90.0, 120.0, "manual", "TARGET_HIT",
        )
        recorder = _RecordingConn(fetchone_results=[])
        recorder._fetchall_results = [[row]]

        @contextmanager
        def _recording_fake_conn():
            yield recorder

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _recording_fake_conn
        ):
            resp = client.get(
                "/api/paper-trading/closed-trades/older?market=US&horizon=medium",
                headers=_auth(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["trades"][0]["symbol"] == "AAPL"
        assert body["trades"][0]["realized_pnl"] == 10.0
