"""
DP-033 readiness audit (2026-07-22) — foreign private issuer / IFRS
classification decision (Option C): a companyfacts response with no
`us-gaap` taxonomy at all (e.g. TSM/CIK 1046179, confirmed live this
session to report only ['dei', 'ifrs-full', 'srt']) is a governed,
documented scope exclusion, not a data-quality defect. This still returns
None (unavailable, never fabricated) from fetch_company_facts() — the
same contract as before — but is now logged/classified distinctly from a
genuinely malformed response, per the readiness-audit requirement that
this boundary be visible and governed, not silently indistinguishable
from a real provider-side break.

No live network access — the exact shape below (['dei', 'ifrs-full',
'srt'], no 'us-gaap') mirrors TSM's real, live-confirmed companyfacts
response this session.
"""
from unittest.mock import MagicMock

import pytest

import services.sec_edgar_adapter as sea


@pytest.mark.unit
class TestIfrsOnlyClassification:
    def test_ifrs_only_payload_still_returns_none_unavailable(self, monkeypatch):
        """Contract unchanged: an IFRS-only filer is UNAVAILABLE for this
        US-GAAP-only adapter, never a fabricated/guessed value."""
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "cik": 1046179, "entityName": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD",
            "facts": {"dei": {}, "ifrs-full": {"Revenue": {}}, "srt": {}},
        }
        monkeypatch.setattr(sea, "_get_with_retry", lambda url: mock_resp)
        sea._facts_cache.clear()
        result = sea.fetch_company_facts(1046179)
        assert result is None

    def test_ifrs_only_classification_is_logged_distinctly(self, monkeypatch, caplog):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "cik": 1046179, "entityName": "TSM",
            "facts": {"dei": {}, "ifrs-full": {"Revenue": {}}, "srt": {}},
        }
        monkeypatch.setattr(sea, "_get_with_retry", lambda url: mock_resp)
        sea._facts_cache.clear()
        with caplog.at_level("INFO"):
            sea.fetch_company_facts(1046179)
        assert any("IFRS" in r.message and "governed exclusion" in r.message for r in caplog.records)
        assert not any("did not match expected shape" in r.message for r in caplog.records)

    def test_genuinely_malformed_payload_still_logs_as_an_error_not_ifrs(self, monkeypatch, caplog):
        """A payload with neither us-gaap nor ifrs-full (a real provider-
        side break, e.g. a schema change) must NOT be silently classified
        as an IFRS exclusion."""
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"cik": 999, "entityName": "X", "facts": {"weird-taxonomy": {}}}
        monkeypatch.setattr(sea, "_get_with_retry", lambda url: mock_resp)
        sea._facts_cache.clear()
        with caplog.at_level("INFO"):
            result = sea.fetch_company_facts(999)
        assert result is None
        assert any("did not match expected shape" in r.message for r in caplog.records)
        assert not any("governed exclusion" in r.message for r in caplog.records)

    def test_us_gaap_payload_unaffected_by_ifrs_classification_logic(self, monkeypatch):
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "cik": 320193, "entityName": "AAPL",
            "facts": {"us-gaap": {"Assets": {"units": {"USD": []}}}},
        }
        monkeypatch.setattr(sea, "_get_with_retry", lambda url: mock_resp)
        sea._facts_cache.clear()
        result = sea.fetch_company_facts(320193)
        assert result is not None
        assert "us-gaap" in result["facts"]
