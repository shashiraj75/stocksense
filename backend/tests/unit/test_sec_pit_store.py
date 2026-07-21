"""
DP-033 remediation — services/sec_pit_store.py, the actual immutable
persisted point-in-time fact store (correcting the prior session's
mischaracterization of sec_edgar_adapter.py's ephemeral 12h in-memory
cache as "immutable persistence" — it is not).

Every test here uses a fresh, isolated SQLite file per test (never the
real sec_pit_facts.db), and NO network access.
"""
import os
import tempfile
from datetime import date

import pytest

import services.sec_pit_store as store


@pytest.fixture(autouse=True)
def _isolated_sqlite(monkeypatch):
    """Fresh temp SQLite file per test — proves persistence survives
    across separate connections (a real process-restart proxy) without
    ever touching the shared dev/test database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # sqlite3.connect creates it fresh
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


@pytest.mark.unit
class TestIngestion:
    def test_ingest_persists_facts(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "0000320193-25-000100"},
        ]})
        result = store.ingest_company_facts(320193, "AAPL", facts)
        assert result["inserted"] == 1
        assert result["skipped"] == 0

    def test_reingesting_the_same_accession_is_idempotent(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "0000320193-25-000100"},
        ]})
        first = store.ingest_company_facts(320193, "AAPL", facts)
        second = store.ingest_company_facts(320193, "AAPL", facts)
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["skipped"] == 1

    def test_a_second_accession_for_the_same_concept_is_a_new_row_not_an_overwrite(self):
        """The core amendment/restatement guarantee: a later filing for
        the same concept is additive, never a replacement."""
        facts_v1 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "0000320193-25-000100"},
        ]})
        facts_v2 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 999.0, "fy": 2025, "fp": "FY", "form": "10-K/A",
             "filed": "2026-01-15", "accn": "0000320193-26-000005"},  # restated value, new accession
        ]})
        store.ingest_company_facts(320193, "AAPL", facts_v1)
        store.ingest_company_facts(320193, "AAPL", facts_v2)
        cov = store.coverage_for_symbol("AAPL", 320193)
        assert cov["n_accessions"] == 2  # both rows retained, neither overwritten

    def test_entries_missing_accession_are_skipped_not_fatal(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},  # no accn
        ]})
        result = store.ingest_company_facts(320193, "AAPL", facts)
        assert result["inserted"] == 0

    def test_only_concepts_sec_edgar_adapter_cares_about_are_persisted(self):
        facts = _companyfacts({
            "NetIncomeLoss": [{"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY",
                               "form": "10-K", "filed": "2025-10-31", "accn": "A-1"}],
            "SomeIrrelevantXbrlConceptNoOneScoresOn": [
                {"end": "2025-09-27", "val": 5.0, "fy": 2025, "fp": "FY", "form": "10-K",
                 "filed": "2025-10-31", "accn": "A-2"},
            ],
        })
        result = store.ingest_company_facts(320193, "AAPL", facts)
        assert result["inserted"] == 1  # only NetIncomeLoss, not the irrelevant concept


@pytest.mark.unit
class TestPersistedAsOfLookupIsOffline:
    def test_lookup_reflects_ingested_data_with_no_network_call(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},  # Wed
        ]})
        store.ingest_company_facts(320193, "AAPL", facts)
        result = store.get_facts_as_of_persisted("AAPL", 320193, date(2025, 10, 3))  # Fri, after next session
        assert result["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"][0]["val"] == 100.0

    def test_lookup_applies_the_conservative_next_trading_session_rule(self):
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-31", "accn": "A-1"},  # Friday
        ]})
        store.ingest_company_facts(320193, "AAPL", facts)
        same_day = store.get_facts_as_of_persisted("AAPL", 320193, date(2025, 10, 31))
        assert same_day["facts"]["us-gaap"] == {}
        next_session = store.get_facts_as_of_persisted("AAPL", 320193, date(2025, 11, 3))
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
        store.ingest_company_facts(320193, "AAPL", facts_v1)
        store.ingest_company_facts(320193, "AAPL", facts_v2)
        # A signal dated before the amendment's filed date must only ever
        # see the original value, never the restated one.
        result = store.get_facts_as_of_persisted("AAPL", 320193, date(2025, 12, 1))
        vals = [e["val"] for e in result["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]]
        assert vals == [100.0]
        assert 999.0 not in vals

    def test_amendment_becomes_visible_once_as_of_passes_its_own_filed_date(self):
        facts_v1 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},
        ]})
        facts_v2 = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 999.0, "fy": 2025, "fp": "FY", "form": "10-K/A",
             "filed": "2026-01-15", "accn": "A-2"},  # Thursday
        ]})
        store.ingest_company_facts(320193, "AAPL", facts_v1)
        store.ingest_company_facts(320193, "AAPL", facts_v2)
        result = store.get_facts_as_of_persisted("AAPL", 320193, date(2026, 1, 20))
        vals = {e["val"] for e in result["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]}
        assert vals == {100.0, 999.0}  # both now present; caller's _best_entry() picks the newer

    def test_no_ingested_data_returns_empty_facts_shape_never_raises(self):
        result = store.get_facts_as_of_persisted("NOTINGESTED", 999999, date(2020, 1, 1))
        assert result["facts"]["us-gaap"] == {}


@pytest.mark.unit
class TestReproducibility:
    def test_repeated_lookup_across_separate_connections_returns_identical_result(self):
        """Simulates process-restart reproducibility: two fully independent
        calls (each opens/closes its own connection, per this module's own
        connection-per-call pattern) against the same persisted data must
        return byte-identical results, unlike the ephemeral in-memory
        cache path which depends on process lifetime."""
        facts = _companyfacts({"NetIncomeLoss": [
            {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY", "form": "10-K",
             "filed": "2025-10-01", "accn": "A-1"},
        ]})
        store.ingest_company_facts(320193, "AAPL", facts)
        first = store.get_facts_as_of_persisted("AAPL", 320193, date(2025, 12, 1))
        second = store.get_facts_as_of_persisted("AAPL", 320193, date(2025, 12, 1))
        assert first == second

    def test_normalize_fields_produces_identical_score_from_persisted_lookup_as_from_live_filtering(self):
        """Cross-checks the persisted-store path against
        sec_edgar_adapter.filter_facts_as_of()'s in-memory path for the
        SAME underlying facts and cutoff -- proves the two share identical
        temporal semantics, not divergent ones."""
        import services.sec_edgar_adapter as sea

        entry = {"end": "2025-09-27", "val": 100.0, "fy": 2025, "fp": "FY",
                  "form": "10-K", "filed": "2025-10-01", "accn": "A-1"}
        facts = _companyfacts({"NetIncomeLoss": [entry]})
        store.ingest_company_facts(320193, "AAPL", facts)

        as_of = date(2025, 12, 1)
        persisted = store.get_facts_as_of_persisted("AAPL", 320193, as_of)
        live_pruned = sea.filter_facts_as_of(facts, as_of)

        persisted_field = sea._extract_direct(persisted, ["NetIncomeLoss"])
        live_field = sea._extract_direct(live_pruned, ["NetIncomeLoss"])
        assert persisted_field["value"] == live_field["value"] == 100.0
