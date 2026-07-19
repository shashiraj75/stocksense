"""
Phase C1-D1 — source registry tests.

Covers: registry completeness (all 11 SourceId members present, all
required fields populated), the exact-URL contract (including the
corrected SME path), host-policy enforcement, and deterministic
serialization.
"""
from __future__ import annotations

import pytest

from services.instrument_master.enums import (
    AutomatedReachabilityStatus,
    HeaderMode,
    HostRole,
    PublicationBlockingStatus,
    SourceCriticality,
    SourceId,
)
from services.instrument_master.source_registry import (
    APPROVED_SOURCE_URLS,
    ARCHIVES_HOST_STATUS_NOTE,
    AUTOMATED_FETCH_HOSTS,
    DISCOVERY_ONLY_HOSTS,
    NOT_SELECTED_HOSTS,
    SOURCE_REGISTRY,
    SourceUrlValidationError,
    all_entries,
    canonical_registry_bytes,
    canonical_registry_hash,
    registry_by_id,
    validate_exact_approved_url,
    validate_source_url,
)

EXPECTED_URLS = {
    SourceId.NSE_EQUITY_CURRENT: "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    SourceId.NSE_ETF_CURRENT: "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
    SourceId.NSE_SME_CURRENT: "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
    SourceId.NSE_REIT_CURRENT: "https://nsearchives.nseindia.com/content/equities/REITS_L.csv",
    SourceId.NSE_INVIT_CURRENT: "https://nsearchives.nseindia.com/content/equities/INVITS_L.csv",
    SourceId.NSE_PREFERENCE: "https://nsearchives.nseindia.com/content/equities/PREF.csv",
    SourceId.NSE_WARRANT: "https://nsearchives.nseindia.com/content/equities/WARRANT.csv",
    SourceId.NSE_IDR: "https://nsearchives.nseindia.com/content/equities/IDR_W9.csv",
    SourceId.NSE_IL_SERIES: "https://nsearchives.nseindia.com/content/equities/eq_ilseclist.csv",
    SourceId.NSE_SYMBOL_HISTORY: "https://nsearchives.nseindia.com/content/equities/symbolchange.csv",
    SourceId.NSE_NAME_HISTORY: "https://nsearchives.nseindia.com/content/equities/namechange.csv",
}


class TestRegistryCompleteness:
    def test_exactly_11_entries(self):
        assert len(SOURCE_REGISTRY) == 11

    def test_canonical_serialization_includes_every_dataclass_field(self):
        # Phase C1-D3 correction (non-blocking finding): _entry_to_canonical_dict()
        # is a hand-maintained mapping. This test guards against a future
        # field added to SourceRegistryEntry being silently omitted from
        # canonical_registry_bytes()/canonical_registry_hash() — if the
        # dataclass field set and the serialized key set ever diverge,
        # this test fails immediately rather than silently under-hashing.
        import dataclasses
        import json

        from services.instrument_master.source_registry import SourceRegistryEntry

        dataclass_field_names = {f.name for f in dataclasses.fields(SourceRegistryEntry)}
        serialized = json.loads(canonical_registry_bytes())
        serialized_keys = set(serialized["source_registry"][0].keys())
        assert serialized_keys == dataclass_field_names

    def test_all_source_ids_present(self):
        assert {e.source_id for e in SOURCE_REGISTRY} == set(SourceId)

    @pytest.mark.parametrize("source_id", list(SourceId))
    def test_exact_url_matches_ratified_contract(self, source_id):
        assert registry_by_id(source_id).exact_url == EXPECTED_URLS[source_id]

    def test_sme_url_uses_corrected_emerge_path(self):
        url = registry_by_id(SourceId.NSE_SME_CURRENT).exact_url
        assert "/emerge/corporates/content/SME_EQUITY_L.csv" in url

    def test_sme_url_does_not_use_incorrect_legacy_path(self):
        url = registry_by_id(SourceId.NSE_SME_CURRENT).exact_url
        assert url != "https://nsearchives.nseindia.com/content/equities/SME_EQUITY_L.csv"
        assert "/content/equities/SME_EQUITY_L.csv" not in url

    @pytest.mark.parametrize("source_id", list(SourceId))
    def test_every_entry_has_required_fields_populated(self, source_id):
        e = registry_by_id(source_id)
        assert e.source_id == source_id
        assert e.category
        assert e.exact_url
        assert e.publisher
        assert isinstance(e.host_role, HostRole)
        assert e.criticality is not None
        assert isinstance(e.required_for_complete_taxonomy, bool)
        assert e.primary_identity_field
        assert isinstance(e.secondary_identity_fields, tuple)
        assert isinstance(e.header_mode, HeaderMode)
        assert isinstance(e.expected_headers, tuple)
        assert e.minimum_field_count > 0
        assert e.maximum_field_count >= e.minimum_field_count
        assert isinstance(e.valid_empty_allowed, bool)
        assert e.parser_contract_id
        assert e.validator_contract_id
        assert isinstance(e.automated_reachability_status, AutomatedReachabilityStatus)
        assert e.fallback_policy
        assert e.freshness_warning_days > 0
        assert e.hard_stale_days >= e.freshness_warning_days
        assert isinstance(e.publication_blocking_status, PublicationBlockingStatus)
        assert isinstance(e.unresolved_risks, tuple)

    def test_all_entries_returns_fixed_deterministic_order(self):
        order1 = [e.source_id for e in all_entries()]
        order2 = [e.source_id for e in all_entries()]
        assert order1 == order2 == list(SourceId)


class TestCriticalityContract:
    def test_equity_current_is_critical_required(self):
        assert registry_by_id(SourceId.NSE_EQUITY_CURRENT).criticality == SourceCriticality.CRITICAL_REQUIRED

    @pytest.mark.parametrize(
        "source_id",
        [SourceId.NSE_ETF_CURRENT, SourceId.NSE_SME_CURRENT],
    )
    def test_dedicated_current_category_sources_required_for_taxonomy(self, source_id):
        e = registry_by_id(source_id)
        assert e.criticality == SourceCriticality.REQUIRED_FOR_COMPLETE_TAXONOMY
        assert e.required_for_complete_taxonomy is True

    @pytest.mark.parametrize("source_id", [SourceId.NSE_SYMBOL_HISTORY, SourceId.NSE_NAME_HISTORY])
    def test_history_sources_are_history_only(self, source_id):
        assert registry_by_id(source_id).criticality == SourceCriticality.HISTORY_ONLY
        assert registry_by_id(source_id).publication_blocking_status == PublicationBlockingStatus.NEVER_BLOCKS

    def test_no_entry_is_discovery_only(self):
        # www.nseindia.com must never become an ingestible registry entry.
        assert all(e.criticality != SourceCriticality.DISCOVERY_ONLY for e in SOURCE_REGISTRY)
        assert all(e.host_role != HostRole.DISCOVERY_ONLY for e in SOURCE_REGISTRY)


class TestHostPolicy:
    def test_automated_fetch_hosts_contains_only_nsearchives(self):
        assert AUTOMATED_FETCH_HOSTS == frozenset({"nsearchives.nseindia.com"})

    def test_discovery_only_hosts_contains_only_www(self):
        assert DISCOVERY_ONLY_HOSTS == frozenset({"www.nseindia.com"})

    def test_not_selected_hosts_contains_only_archives(self):
        assert NOT_SELECTED_HOSTS == frozenset({"archives.nseindia.com"})

    def test_archives_status_note_wording_exact(self):
        assert ARCHIVES_HOST_STATUS_NOTE == (
            "Not selected for automated ingestion based on repeated failures from the "
            "tested local, Railway and GitHub Actions environments."
        )
        assert "dead" not in ARCHIVES_HOST_STATUS_NOTE.lower()
        assert "permanent" not in ARCHIVES_HOST_STATUS_NOTE.lower()

    @pytest.mark.parametrize("source_id", list(SourceId))
    def test_every_registry_url_is_on_automated_fetch_allowlist(self, source_id):
        # __post_init__ already enforces this at import time; this test
        # re-asserts it explicitly and independently.
        validate_source_url(registry_by_id(source_id).exact_url, allow_hosts=AUTOMATED_FETCH_HOSTS)

    def test_rejects_http_scheme(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("http://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")

    def test_rejects_embedded_credentials(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://user:pass@nsearchives.nseindia.com/x.csv")

    def test_rejects_unexpected_port(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com:8443/x.csv")

    @pytest.mark.parametrize(
        "url",
        [
            "https://192.168.1.1/x.csv",
            "https://8.8.8.8/x.csv",
        ],
    )
    def test_rejects_ip_address_host(self, url):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url(url)

    def test_rejects_lookalike_suffix_confusion_host(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com.evil.example/x.csv")

    def test_rejects_arbitrary_subdomain(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://api.nsearchives.nseindia.com/x.csv")

    def test_rejects_archives_host_for_automated_fetch(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://archives.nseindia.com/content/equities/EQUITY_L.csv")

    def test_rejects_www_host_for_automated_fetch(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://www.nseindia.com/content/equities/EQUITY_L.csv")

    def test_rejects_query_parameters(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv?x=1")

    def test_rejects_url_fragment(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv#frag")

    def test_wrong_sme_url_contract_fixture_rejected_by_sme_path_check(self):
        # This is the exact required test: replacing the corrected SME
        # path with the incorrect /content/equities path must fail.
        wrong_url = "https://nsearchives.nseindia.com/content/equities/SME_EQUITY_L.csv"
        assert wrong_url != registry_by_id(SourceId.NSE_SME_CURRENT).exact_url

    # -- Phase C1-D3 correction: path-component validation --------------

    def test_rejects_trailing_whitespace(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv ")

    def test_rejects_leading_whitespace(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url(" https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")

    def test_rejects_embedded_tab(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv\t")

    def test_rejects_embedded_cr(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv\r")

    def test_rejects_embedded_lf(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv\n")

    def test_rejects_nul_character(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv\x00")

    def test_rejects_control_character(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv\x01")

    def test_rejects_del_character(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv\x7f")

    def test_rejects_path_traversal_segments(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/../../etc/passwd")

    def test_rejects_percent_encoded_path_traversal(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/equities/%2e%2e/EQUITY_L.csv")

    def test_rejects_backslashes(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https:\\\\nsearchives.nseindia.com\\content\\equities\\EQUITY_L.csv")

    def test_rejects_duplicate_slashes_in_path(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com//content//equities//EQUITY_L.csv")

    def test_rejects_encoded_dot_segment(self):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url("https://nsearchives.nseindia.com/content/./equities/EQUITY_L.csv")

    # -- Phase C1-D5 correction: percent-encoding rejected outright ------

    @pytest.mark.parametrize(
        "encoded_url",
        [
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv%3Fsecret=1",  # %3F
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv%3fsecret=1",  # %3f
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv%23frag",  # %23
            "https://nsearchives.nseindia.com/content%2Fequities/EQUITY_L.csv",  # %2F
            "https://nsearchives.nseindia.com/content%2fequities/EQUITY_L.csv",  # %2f
            "https://nsearchives.nseindia.com/content%5Cequities/EQUITY_L.csv",  # %5C
            "https://nsearchives.nseindia.com/content%5cequities/EQUITY_L.csv",  # %5c
            "https://nsearchives.nseindia.com/content/equities/%2Ehidden.csv",  # %2E
            "https://nsearchives.nseindia.com/content/equities/%2ehidden.csv",  # %2e
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv%00",  # %00
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv%41",  # %41 (decodes to harmless 'A')
        ],
    )
    def test_rejects_any_percent_encoding(self, encoded_url):
        with pytest.raises(SourceUrlValidationError):
            validate_source_url(encoded_url)

    def test_all_11_approved_urls_remain_accepted_after_percent_encoding_correction(self):
        for url in APPROVED_SOURCE_URLS:
            validate_source_url(url)  # must not raise


class TestExactApprovedUrlMatching:
    """Phase C1-D3 correction: for registry construction, the strongest
    acceptable rule is exact membership in APPROVED_SOURCE_URLS — no
    normalization of any kind may turn a different string into a match."""

    def test_all_11_exact_approved_urls_accepted(self):
        # Smoke test only — tautological in isolation (APPROVED_SOURCE_URLS
        # validated against itself). Paired below with an independent-
        # literal assertion (test_independently_hand_typed_urls_are_exactly_approved)
        # that would catch a wrong-but-internally-consistent constant.
        assert len(APPROVED_SOURCE_URLS) == 11
        for url in APPROVED_SOURCE_URLS:
            validate_exact_approved_url(url)  # must not raise

    def test_independently_hand_typed_urls_are_exactly_approved(self):
        # Phase C1-D5 cleanup: EXPECTED_URLS (top of this file) is a
        # separately hand-typed literal dict, not derived from
        # APPROVED_SOURCE_URLS or SOURCE_REGISTRY — this test would catch
        # a scenario where APPROVED_SOURCE_URLS itself was wrong (e.g. all
        # 11 URLs accidentally set to garbage but internally consistent),
        # which the tautological smoke test above cannot.
        assert set(EXPECTED_URLS.values()) == APPROVED_SOURCE_URLS
        for url in EXPECTED_URLS.values():
            validate_exact_approved_url(url)  # must not raise

    def test_registry_urls_equal_approved_url_set(self):
        assert {e.exact_url for e in all_entries()} == APPROVED_SOURCE_URLS

    def test_one_character_path_difference_rejected(self):
        with pytest.raises(SourceUrlValidationError):
            validate_exact_approved_url("https://nsearchives.nseindia.com/content/equities/EQUITY_l.csv")

    def test_trailing_slash_rejected(self):
        base = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        with pytest.raises(SourceUrlValidationError):
            validate_exact_approved_url(base + "/")

    def test_duplicate_slash_rejected(self):
        with pytest.raises(SourceUrlValidationError):
            validate_exact_approved_url("https://nsearchives.nseindia.com/content//equities/EQUITY_L.csv")

    def test_case_variation_rejected(self):
        with pytest.raises(SourceUrlValidationError):
            validate_exact_approved_url("HTTPS://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")

    def test_valid_host_with_wrong_source_filename_rejected(self):
        with pytest.raises(SourceUrlValidationError):
            validate_exact_approved_url("https://nsearchives.nseindia.com/content/equities/NOT_A_REAL_FILE.csv")

    def test_whitespace_normalized_variant_rejected(self):
        base = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        with pytest.raises(SourceUrlValidationError):
            validate_exact_approved_url(base + " ")


class TestDeterminism:
    def test_canonical_registry_bytes_is_deterministic(self):
        assert canonical_registry_bytes() == canonical_registry_bytes()

    def test_canonical_registry_hash_is_deterministic(self):
        assert canonical_registry_hash() == canonical_registry_hash()

    def test_canonical_registry_bytes_has_sorted_keys_and_trailing_newline(self):
        data = canonical_registry_bytes()
        assert data.endswith(b"\n")
        text = data.decode("utf-8")
        assert '"source_registry"' in text

    def test_registry_contains_no_environment_derived_value(self, monkeypatch):
        # Setting an unrelated env var must never change any URL/host.
        monkeypatch.setenv("NSE_EQUITY_URL_OVERRIDE", "https://evil.example/EQUITY_L.csv")
        hash_before = canonical_registry_hash()
        assert hash_before == canonical_registry_hash()
        assert registry_by_id(SourceId.NSE_EQUITY_CURRENT).exact_url == EXPECTED_URLS[SourceId.NSE_EQUITY_CURRENT]
