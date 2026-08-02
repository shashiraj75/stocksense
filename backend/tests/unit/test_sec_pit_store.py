"""
DP-033 remediation — services/sec_pit_store.py, the actual immutable
persisted point-in-time fact store, corrected fact identity (period_start
added, second readiness pass), and acquisition/replay separation.

Every test here uses a fresh, isolated SQLite file per test (never the
real sec_pit_facts.db), and NO network access — acquisition is always
exercised via injected `resolve_cik_fn`/`fetch_company_facts_fn`.
"""
import os
import tempfile
from datetime import date

import pytest

import services.sec_pit_store as store


@pytest.fixture(autouse=True)
def _isolated_sqlite(monkeypatch):
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


def _companyfacts(entries_by_concept: dict) -> dict:
    return {
        "cik": 320193, "entityName": "TEST CORP",
        "facts": {"us-gaap": {c: {"units": {"USD": e}} for c, e in entries_by_concept.items()}},
    }


def _ingest(symbol="AAPL", cik=320193, facts=None, run_id="run_1", manifest="manifest_v1"):
    return store.ingest_symbol(
        symbol, run_id, manifest,
        resolve_cik_fn=lambda sym: cik,
        fetch_company_facts_fn=lambda c: facts if facts is not None else _companyfacts({}),
    )


@pytest.mark.unit
class TestIngestSymbol:
    def test_ingest_persists_facts_and_completes(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "0000320193-25-000100"},
        ]})
        result = _ingest(facts=facts)
        assert result["completion_status"] == "complete"
        assert result["fact_rows_inserted"] == 1
        assert result["duplicates_skipped"] == 0

    def test_reingesting_the_same_run_and_symbol_is_idempotent(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "A-1"},
        ]})
        first = _ingest(facts=facts, run_id="run_1")
        second = _ingest(facts=facts, run_id="run_2")  # different run, same fact data
        assert first["fact_rows_inserted"] == 1
        assert second["fact_rows_inserted"] == 0
        assert second["duplicates_skipped"] == 1

    def test_a_second_accession_for_the_same_concept_is_a_new_row_not_an_overwrite(self):
        facts_v1 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "A-1"},
        ]})
        facts_v2 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 999.0, "fy": 2025, "fp": "FY", "form": "10-K/A",
             "filed": "2026-01-15", "accn": "A-2"},
        ]})
        _ingest(facts=facts_v1, run_id="run_1")
        _ingest(facts=facts_v2, run_id="run_2")
        cov = store.coverage_for_symbol("AAPL", 320193)
        assert cov["n_accessions"] == 2

    def test_entries_missing_accession_are_skipped_not_fatal(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
        ]})
        result = _ingest(facts=facts)
        assert result["fact_rows_inserted"] == 0
        assert result["malformed_skipped"] == 1

    def test_only_concepts_sec_edgar_adapter_cares_about_are_persisted(self):
        facts = _companyfacts({
            "NetIncomeLoss": [{"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY",
                               "form": "10-K", "filed": "2025-10-31", "accn": "A-1"}],
            "SomeIrrelevantXbrlConceptNoOneScoresOn": [
                {"end": "2025-09-27", "val": 5.0, "fy": 2025, "fp": "FY", "form": "10-K",
                 "filed": "2025-10-31", "accn": "A-2"},
            ],
        })
        result = _ingest(facts=facts)
        assert result["fact_rows_inserted"] == 1
        assert result["fact_rows_observed"] == 1  # irrelevant concept never counted at all

    def test_cik_not_found_marks_failed_no_registry_entry(self):
        result = store.ingest_symbol(
            "NOTFOUND", "run_1", "manifest_v1",
            resolve_cik_fn=lambda sym: None,
            fetch_company_facts_fn=lambda cik: _companyfacts({}),
        )
        assert result["completion_status"] == "failed"
        assert result["failure_reason"] == "CIK not found"
        assert store.get_symbol_registry_entry("NOTFOUND") is None

    def test_period_start_collision_both_ytd_and_quarterly_facts_survive(self):
        """The core fix this pass: a single accession with the same
        concept/period_end but DIFFERENT period_start (e.g. YTD vs.
        quarter-only, the real, live-confirmed SEC pattern for
        NetIncomeLoss and similar duration concepts) must both survive as
        distinct rows. Uses NetIncomeLoss (a tracked concept) rather than
        the real-world example concept (AllocatedShareBasedCompensation
        Expense, not in _DIRECT_FIELD_TAGS and so correctly never
        persisted at all) to isolate this specific behavior."""
        facts = _companyfacts({"NetIncomeLoss": [
            {"start": "2012-09-30", "end": "2013-06-29", "val": 1698000000, "fy": 2014,
             "fp": "Q3", "form": "10-Q", "filed": "2014-07-23", "accn": "A-1"},
            {"start": "2013-03-31", "end": "2013-06-29", "val": 578000000, "fy": 2014,
             "fp": "Q3", "form": "10-Q", "filed": "2014-07-23", "accn": "A-1"},
        ]})
        result = _ingest(facts=facts)
        assert result["fact_rows_inserted"] == 2  # both survive -- old key would have collapsed to 1

    def test_instant_facts_with_no_period_start_still_deduplicate_correctly(self):
        """The second real bug this session's own tests caught: NULL is
        never equal to NULL in a SQL UNIQUE constraint, so an instant
        fact (Assets/Liabilities/StockholdersEquity -- genuinely no
        "start" key at all) would silently duplicate on every
        re-ingestion instead of deduplicating. Proves the fix (a non-NULL
        sentinel, normalized back to None on read)."""
        facts = _companyfacts({"Assets": [
            {"end": "2025-09-27", "val": 500.0, "fy": 2025, "fp": "FY",
             "form": "10-K", "filed": "2025-10-31", "accn": "A-1"},  # no "start" key -- instant fact
        ]})
        first = _ingest(facts=facts, run_id="run_1")
        second = _ingest(facts=facts, run_id="run_2")
        assert first["fact_rows_inserted"] == 1
        assert second["fact_rows_inserted"] == 0  # must dedupe, not silently double
        cov = store.coverage_for_symbol("AAPL", 320193)
        assert cov["n_facts"] == 1

    def test_instant_fact_start_reads_back_as_none_not_the_internal_sentinel(self):
        facts = _companyfacts({"Assets": [
            {"end": "2025-09-27", "val": 500.0, "fy": 2025, "fp": "FY",
             "form": "10-K", "filed": "2025-10-01", "accn": "A-1"},
        ]})
        _ingest(facts=facts)
        result = store.get_facts_as_of_replay("AAPL", date(2025, 12, 1))
        entry = result["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]
        assert entry["start"] is None  # never the raw "" sentinel leaking to a caller


@pytest.mark.unit
class TestReplayIsOfflineAndPure:
    def test_lookup_reflects_ingested_data(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},  # Wed
        ]})
        _ingest(facts=facts)
        result = store.get_facts_as_of_replay("AAPL", date(2025, 10, 3))
        assert result["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"][0]["val"] == 100.0

    def test_applies_conservative_next_trading_session_rule(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "A-1"},  # Friday
        ]})
        _ingest(facts=facts)
        same_day = store.get_facts_as_of_replay("AAPL", date(2025, 10, 31))
        assert same_day["facts"]["us-gaap"] == {}
        next_session = store.get_facts_as_of_replay("AAPL", date(2025, 11, 3))
        assert next_session["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]

    def test_amendment_filed_after_cutoff_does_not_leak_backward(self):
        facts_v1 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},
        ]})
        facts_v2 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 999.0, "fy": 2025, "fp": "FY", "form": "10-K/A",
             "filed": "2026-01-15", "accn": "A-2"},
        ]})
        _ingest(facts=facts_v1, run_id="run_1")
        _ingest(facts=facts_v2, run_id="run_2")
        result = store.get_facts_as_of_replay("AAPL", date(2025, 12, 1))
        vals = [e["val"] for e in result["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]]
        assert vals == [100.0]

    def test_never_ingested_symbol_returns_empty_shape_never_raises(self):
        result = store.get_facts_as_of_replay("NOTINGESTED", date(2020, 1, 1))
        assert result["facts"]["us-gaap"] == {}
        assert result["cik"] is None


@pytest.mark.unit
class TestIngestionCompletenessContract:
    def test_get_ingestion_status_reports_complete(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},
        ]})
        _ingest(facts=facts)
        status = store.get_ingestion_status("AAPL")
        assert status["completion_status"] == "complete"

    def test_get_ingestion_status_none_for_never_ingested(self):
        assert store.get_ingestion_status("NEVERINGESTED") is None

    def test_get_ingestion_status_reports_failed(self):
        store.ingest_symbol("BAD", "run_1", "manifest_v1",
                             resolve_cik_fn=lambda sym: None,
                             fetch_company_facts_fn=lambda cik: None)
        status = store.get_ingestion_status("BAD")
        assert status["completion_status"] == "failed"


@pytest.mark.unit
class TestReproducibility:
    def test_repeated_lookup_across_separate_connections_returns_identical_result(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},
        ]})
        _ingest(facts=facts)
        first = store.get_facts_as_of_replay("AAPL", date(2025, 12, 1))
        second = store.get_facts_as_of_replay("AAPL", date(2025, 12, 1))
        assert first == second
