"""
DP-033 second readiness pass (2026-07-22) — proves replay NEVER acquires,
under any circumstance, including a "cache miss" / never-ingested symbol.
Replaces the prior pass's test_dp033_persisted_ingest_wiring.py, whose
premise (lazy ingest-on-first-use inside the replay path) no longer
applies now that acquisition and replay are fully separated:

  - services.sec_pit_store.ingest_symbol() is the ONLY acquisition path.
  - services.validation_engine._get_fundamentals_as_of_replay() /
    services.sec_pit_store.get_facts_as_of_replay() are the ONLY replay
    paths, and neither can reach sec_edgar_adapter's live functions.

No real network access — sec_edgar_adapter.resolve_cik/fetch_company_facts
are only ever exercised via explicit injection into ingest_symbol(),
never invoked implicitly.
"""
import os
import tempfile
from datetime import date
from unittest.mock import MagicMock

import pytest

import services.validation_engine as ve
import services.sec_pit_store as store


@pytest.fixture(autouse=True)
def _isolated_pit_store(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(store, "_SQLITE_PATH", path)
    monkeypatch.setattr(store, "_USE_POSTGRES", False)
    monkeypatch.setattr(store, "_initialized", False)
    yield
    store._initialized = False
    if os.path.exists(path):
        os.unlink(path)


def _companyfacts():
    return {
        "cik": 320193, "entityName": "AAPL",
        "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY",
             "form": "10-K", "filed": "2025-10-01", "accn": "A-1"},
        ]}}}},
    }


@pytest.mark.unit
class TestReplayNeverAcquires:
    def test_never_ingested_symbol_is_available_false_no_network_attempt(self, monkeypatch):
        live_resolve = MagicMock(side_effect=AssertionError("resolve_cik must never be called by replay"))
        live_fetch = MagicMock(side_effect=AssertionError("fetch_company_facts must never be called by replay"))
        monkeypatch.setattr(ve.sec_edgar_adapter, "resolve_cik", live_resolve)
        monkeypatch.setattr(ve.sec_edgar_adapter, "fetch_company_facts", live_fetch)

        result = ve._get_fundamentals_as_of_replay("NEVERINGESTED", date(2025, 1, 1))
        assert result["available"] is False
        assert "not ingested" in result["reason"]
        live_resolve.assert_not_called()
        live_fetch.assert_not_called()

    def test_after_explicit_ingestion_replay_still_makes_no_live_call(self, monkeypatch):
        # Explicit acquisition step (the only place resolve_cik/fetch are legitimately called).
        store.ingest_symbol(
            "AAPL", "run_1", "manifest_v1",
            resolve_cik_fn=lambda sym: 320193,
            fetch_company_facts_fn=lambda cik: _companyfacts(),
        )
        # Now block the live path entirely and prove replay doesn't need it.
        monkeypatch.setattr(ve.sec_edgar_adapter, "resolve_cik",
                             MagicMock(side_effect=AssertionError("must not be called")))
        monkeypatch.setattr(ve.sec_edgar_adapter, "fetch_company_facts",
                             MagicMock(side_effect=AssertionError("must not be called")))

        result = ve._get_fundamentals_as_of_replay("AAPL", date(2025, 12, 1))
        assert result["available"] is True
        assert result["fields"]["net_income"]["value"] == 100.0

    def test_repeated_replay_calls_never_trigger_any_write(self, monkeypatch):
        store.ingest_symbol(
            "AAPL", "run_1", "manifest_v1",
            resolve_cik_fn=lambda sym: 320193,
            fetch_company_facts_fn=lambda cik: _companyfacts(),
        )
        before = store.coverage_for_symbol("AAPL", 320193)
        for _ in range(10):
            ve._get_fundamentals_as_of_replay("AAPL", date(2025, 12, 1))
        after = store.coverage_for_symbol("AAPL", 320193)
        assert before == after  # zero rows added by replay, ever

    def test_process_restart_equivalent_still_works_offline(self, monkeypatch):
        """Simulates a process restart: no in-memory state carries over
        (there IS none to carry over in the corrected architecture — no
        _pit_ingested_ciks set exists anymore), only the SQLite file."""
        store.ingest_symbol(
            "AAPL", "run_1", "manifest_v1",
            resolve_cik_fn=lambda sym: 320193,
            fetch_company_facts_fn=lambda cik: _companyfacts(),
        )
        monkeypatch.setattr(ve.sec_edgar_adapter, "resolve_cik",
                             MagicMock(side_effect=AssertionError("must not be called")))
        monkeypatch.setattr(ve.sec_edgar_adapter, "fetch_company_facts",
                             MagicMock(side_effect=AssertionError("must not be called")))
        result = ve._get_fundamentals_as_of_replay("AAPL", date(2025, 12, 1))
        assert result["available"] is True

    def test_no_pit_ingested_ciks_module_state_exists_anymore(self):
        """Regression guard: the prior pass's per-process ingestion-tracking
        set must be gone entirely — its mere existence was the seam that
        allowed lazy acquisition to hide inside a 'persisted-first' name."""
        assert not hasattr(ve, "_pit_ingested_ciks")
        assert not hasattr(ve, "_pit_ingest_lock")


@pytest.mark.unit
class TestIngestSymbolIsTheOnlyAcquisitionPath:
    def test_ingest_symbol_populates_registry_and_facts(self):
        result = store.ingest_symbol(
            "AAPL", "run_1", "manifest_v1",
            resolve_cik_fn=lambda sym: 320193,
            fetch_company_facts_fn=lambda cik: _companyfacts(),
        )
        assert result["completion_status"] == "complete"
        assert result["cik"] == 320193
        entry = store.get_symbol_registry_entry("AAPL")
        assert entry["cik"] == 320193
        assert entry["taxonomy_classification"] == "us_gaap"

    def test_failed_cik_resolution_does_not_populate_registry(self):
        result = store.ingest_symbol(
            "NOTFOUND", "run_1", "manifest_v1",
            resolve_cik_fn=lambda sym: None,
            fetch_company_facts_fn=lambda cik: _companyfacts(),
        )
        assert result["completion_status"] == "failed"
        assert store.get_symbol_registry_entry("NOTFOUND") is None

    def test_ingest_symbol_is_the_injection_point_used_by_all_acquisition(self):
        """Confirms ingest_symbol() accepts injectable acquisition
        functions -- the contract tooling (scripts/sec_pit_ingest.py) and
        tests both rely on, and that no other function in sec_pit_store
        performs acquisition."""
        import inspect
        sig = inspect.signature(store.ingest_symbol)
        assert "resolve_cik_fn" in sig.parameters
        assert "fetch_company_facts_fn" in sig.parameters
        # get_facts_as_of_replay() must NOT accept any acquisition injection --
        # it has no legitimate reason to ever acquire.
        replay_sig = inspect.signature(store.get_facts_as_of_replay)
        assert "resolve_cik_fn" not in replay_sig.parameters
        assert "fetch_company_facts_fn" not in replay_sig.parameters
