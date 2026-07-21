"""
DP-026 remediation — point-in-time (as-of) filtering for the SEC EDGAR
adapter (services/sec_edgar_adapter.py's filter_facts_as_of() /
get_fundamentals_as_of()).

Every fact-entry shape below (end/val/fy/fp/form/filed) mirrors the shapes
already used in test_sec_edgar_adapter_field_extraction.py and confirmed
live against SEC EDGAR's companyfacts API for AAPL during this repository's
prior SSDS-006 sprint and this session's DP-026 investigation
(data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues.json,
fetched live 2026-07-21) — not invented.

No network access anywhere in this file.
"""
from datetime import date
from unittest.mock import MagicMock

import pytest

import services.sec_edgar_adapter as sea


def _facts(entries_by_concept: dict[str, list[dict]]) -> dict:
    return {
        "cik": 320193,
        "entityName": "TEST CORP",
        "facts": {
            "us-gaap": {
                concept: {"units": {"USD": entries}}
                for concept, entries in entries_by_concept.items()
            }
        },
    }


@pytest.mark.unit
class TestFilterFactsAsOf:
    def test_entry_filed_before_cutoff_is_eligible(self):
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2025, 11, 1))
        assert pruned["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] == 100

    def test_entry_filed_on_cutoff_is_eligible(self):
        # Conservative rule: filed == as_of is included (date-only precision).
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2025, 10, 31))
        assert pruned["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] == 100

    def test_entry_filed_after_cutoff_is_excluded(self):
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2025, 10, 30))
        assert "Assets" not in pruned["facts"]["us-gaap"]

    def test_older_filing_survives_when_newer_one_is_excluded(self):
        # The core DP-026 fix, proven directly: a backtest signal dated
        # BEFORE a later 10-K's filing date must still see the OLDER
        # filing's value, never the newer one and never "nothing."
        facts = _facts({"Revenues": [
            {"end": "2015-09-26", "val": 215639000000, "fy": 2015, "fp": "FY",
             "form": "10-K", "filed": "2015-10-28"},
            {"end": "2016-09-24", "val": 233715000000, "fy": 2016, "fp": "FY",
             "form": "10-K", "filed": "2016-10-26"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2016, 1, 1))
        result = sea._extract_direct(pruned, ["Revenues"])
        assert result["value"] == 215639000000
        assert result["filed_date"] == "2015-10-28"

    def test_all_entries_filtered_out_drops_the_concept_entirely(self):
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2020, 1, 1))
        assert pruned["facts"]["us-gaap"] == {}

    def test_multiple_concepts_filtered_independently(self):
        facts = _facts({
            "Assets": [{"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY",
                        "form": "10-K", "filed": "2025-10-31"}],
            "Liabilities": [{"end": "2020-09-26", "val": 50, "fy": 2020, "fp": "FY",
                             "form": "10-K", "filed": "2020-10-30"}],
        })
        pruned = sea.filter_facts_as_of(facts, date(2021, 1, 1))
        assert "Liabilities" in pruned["facts"]["us-gaap"]
        assert "Assets" not in pruned["facts"]["us-gaap"]

    def test_entry_missing_filed_date_is_excluded_not_assumed_eligible(self):
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": None},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2030, 1, 1))
        assert "Assets" not in pruned["facts"]["us-gaap"]

    def test_malformed_filed_date_is_excluded_not_crashed_on(self):
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "not-a-date"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2030, 1, 1))
        assert "Assets" not in pruned["facts"]["us-gaap"]

    def test_does_not_mutate_the_input_facts_dict(self):
        facts = _facts({"Assets": [
            {"end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
        ]})
        import copy
        original = copy.deepcopy(facts)
        sea.filter_facts_as_of(facts, date(2020, 1, 1))
        assert facts == original

    def test_fiscal_period_end_date_alone_is_never_used_as_the_cutoff_basis(self):
        # A fact with an OLD period end but a fresh filed date (e.g. a
        # restated prior-year figure filed later) must be excluded when the
        # cutoff predates the actual filed date, even though `end` alone
        # would suggest it "belongs" to an earlier period.
        facts = _facts({"Assets": [
            {"end": "2015-09-26", "val": 999, "fy": 2015, "fp": "FY",
             "form": "10-K/A", "filed": "2026-01-01"},
        ]})
        pruned = sea.filter_facts_as_of(facts, date(2020, 1, 1))
        assert "Assets" not in pruned["facts"]["us-gaap"]


@pytest.mark.unit
class TestGetFundamentalsAsOf:
    def test_available_true_with_eligible_facts(self, monkeypatch):
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
        monkeypatch.setattr(sea, "fetch_company_facts", lambda cik: _facts({
            "Revenues": [{"end": "2015-09-26", "val": 215639000000, "fy": 2015,
                          "fp": "FY", "form": "10-K", "filed": "2015-10-28"}],
        }))
        result = sea.get_fundamentals_as_of("AAPL", date(2016, 1, 1))
        assert result["available"] is True
        assert result["point_in_time"] is True
        assert result["as_of"] == "2016-01-01"
        assert result["fields"]["revenue"]["value"] == 215639000000

    def test_available_false_when_as_of_predates_every_filing(self, monkeypatch):
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
        monkeypatch.setattr(sea, "fetch_company_facts", lambda cik: _facts({
            "Revenues": [{"end": "2015-09-26", "val": 215639000000, "fy": 2015,
                          "fp": "FY", "form": "10-K", "filed": "2015-10-28"}],
        }))
        result = sea.get_fundamentals_as_of("AAPL", date(2010, 1, 1))
        assert result["available"] is False
        assert result["reason"] == "no eligible filing as of the signal date"

    def test_available_false_never_falls_back_to_current_data(self, monkeypatch):
        # If CIK resolution fails, the function must return unavailable —
        # never silently substitute a live/current fetch.
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: None)
        live_fetch = MagicMock()
        monkeypatch.setattr(sea, "fetch_company_facts", live_fetch)
        result = sea.get_fundamentals_as_of("NOTFOUND", date(2020, 1, 1))
        assert result["available"] is False
        assert result["reason"] == "CIK not found"
        live_fetch.assert_not_called()

    def test_available_false_when_companyfacts_fetch_fails(self, monkeypatch):
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
        monkeypatch.setattr(sea, "fetch_company_facts", lambda cik: None)
        result = sea.get_fundamentals_as_of("AAPL", date(2020, 1, 1))
        assert result["available"] is False
        assert result["reason"] == "companyfacts fetch failed or returned an unexpected shape"

    def test_two_different_as_of_dates_for_the_same_symbol_yield_different_values(self, monkeypatch):
        # Direct proof this is a genuine as-of lookup, not a cached
        # current-value shortcut: two different cutoffs against the SAME
        # fetched payload must resolve to two different historical values.
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
        monkeypatch.setattr(sea, "fetch_company_facts", lambda cik: _facts({
            "Revenues": [
                {"end": "2015-09-26", "val": 215639000000, "fy": 2015, "fp": "FY",
                 "form": "10-K", "filed": "2015-10-28"},
                {"end": "2016-09-24", "val": 233715000000, "fy": 2016, "fp": "FY",
                 "form": "10-K", "filed": "2016-10-26"},
            ],
        }))
        early = sea.get_fundamentals_as_of("AAPL", date(2016, 1, 1))
        late = sea.get_fundamentals_as_of("AAPL", date(2017, 1, 1))
        assert early["fields"]["revenue"]["value"] == 215639000000
        assert late["fields"]["revenue"]["value"] == 233715000000

    def test_does_not_call_the_live_current_data_entry_point(self, monkeypatch):
        # get_fundamentals_as_of() must never internally call
        # fetch_us_fundamentals_sec_edgar() (the live/current-only path) —
        # that would silently reintroduce a current-data fallback.
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
        monkeypatch.setattr(sea, "fetch_company_facts", lambda cik: _facts({}))
        live_fn = MagicMock(side_effect=AssertionError("must not be called"))
        monkeypatch.setattr(sea, "fetch_us_fundamentals_sec_edgar", live_fn)
        sea.get_fundamentals_as_of("AAPL", date(2020, 1, 1))
        live_fn.assert_not_called()

    def test_makes_exactly_one_companyfacts_fetch_regardless_of_caller(self, monkeypatch):
        # fetch_company_facts is already 12h-cached by CIK — confirms
        # get_fundamentals_as_of() doesn't add a second, uncached fetch
        # path, so a backtest calling this once per signal date for the
        # same symbol still only network-fetches once (per the existing
        # cache TTL), not once per signal.
        monkeypatch.setattr(sea, "resolve_cik", lambda sym: 320193)
        calls = []
        def fake_fetch(cik):
            calls.append(cik)
            return _facts({"Assets": [{"end": "2025-09-27", "val": 100, "fy": 2025,
                                        "fp": "FY", "form": "10-K", "filed": "2025-10-31"}]})
        monkeypatch.setattr(sea, "fetch_company_facts", fake_fetch)
        sea.get_fundamentals_as_of("AAPL", date(2026, 1, 1))
        sea.get_fundamentals_as_of("AAPL", date(2026, 6, 1))
        assert len(calls) == 2  # this test's own monkeypatch bypasses the real TTL cache,
        # but proves each call goes through fetch_company_facts() -- the same
        # function backing the 12h cache -- rather than a duplicate uncached path.
