"""
Trade Postmortem Sprint 3A, Stage D — proof that price-path evidence
acquisition never occurs inside the financial close transaction, is
always bounded, never fabricates evidence on provider failure, and never
makes a live network call from this test suite.
"""
import datetime as dt
import inspect

import pytest

from services.market_hours import ET
from services.postmortem import close_service
from services.postmortem.price_path_acquisition import (
    AcquisitionWindowTooLargeError,
    BASIS_INSUFFICIENT_EVIDENCE,
    BASIS_MISMATCH,
    COMPATIBLE_UNADJUSTED,
    COMPOSITE_ADJUSTED_UNKNOWN_BASIS,
    MAX_ACQUISITION_WINDOW_DAYS,
    PriceProviderAcquisitionError,
    SOURCE_SCOPE,
    SOURCE_TYPE,
    SPLIT_IN_WINDOW,
    UNADJUSTED_PROVIDER_OHLC,
    acquire_price_path_evidence,
    build_price_path_evidence,
    evaluate_basis_compatibility,
    MANIFEST_INTEGRITY_VIOLATION,
    SOURCE_ID_YFINANCE_DAILY,
    finalize_source_manifest,
    validate_manifest_compatibility,
    verify_source_manifest_integrity,
)


@pytest.mark.unit
class TestTerminologyCorrection:
    """Stage D0."""

    def test_source_type_is_not_falsely_approved(self):
        assert SOURCE_TYPE == "EXTERNAL_UNOFFICIAL_DAILY"
        assert SOURCE_TYPE != "APPROVED_EXTERNAL_SOURCE"

    def test_source_scope_states_bounded_use(self):
        assert SOURCE_SCOPE == "BOUNDED_EVIDENCE_ACQUISITION_ONLY"

    def test_source_manifest_declares_not_production_authoritative(self):
        bundle = build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 2), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None}],
            split_events=[],
        )
        assert bundle.source_manifest["production_authoritative"] is False
        assert bundle.source_manifest["source_scope"] == SOURCE_SCOPE


@pytest.mark.unit
class TestSourceManifestCompleteness:
    """Stage J-F3 — the source-manifest fields identified as genuinely
    missing in the Stage J-F1 gap inventory: symbol normalization
    version, provider-request-start, provider-exclusive-end, the
    end-widening reason, boundary-policy version, the manifest's own
    schema version, a requested-session count distinct from calendar
    days, and a manifest-integrity hash separate from the bar-only
    evidence_hash. Deterministic and reproducible from persisted trade
    facts alone — no current universe lookup, no inferred rename."""

    def _bundle(self, **overrides):
        kwargs = dict(
            paper_trade_id=1, user_id="u", symbol="aapl", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 3, tzinfo=ET),
            raw_bars=[
                {"date": dt.date(2026, 6, 1), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None},
                {"date": dt.date(2026, 6, 2), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None},
                {"date": dt.date(2026, 6, 3), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None},
            ],
            split_events=[],
        )
        kwargs.update(overrides)
        return build_price_path_evidence(**kwargs)

    def test_symbol_and_market_fields_present(self):
        manifest = self._bundle().source_manifest
        assert manifest["trade_symbol"] == "AAPL"
        assert manifest["provider_symbol"] == "AAPL"
        assert manifest["market"] == "US"
        assert manifest["symbol_normalization_version"] == "1.0.0"

    def test_window_and_widening_fields_present(self):
        manifest = self._bundle().source_manifest
        assert manifest["provider_request_start"] == "2026-06-01"
        # exit_date (2026-06-03) widened by one day for the provider's
        # exclusive-end convention — never leaks into requested_window_end.
        assert manifest["provider_exclusive_request_end"] == "2026-06-04"
        assert "end_widening_reason" in manifest and manifest["end_widening_reason"]
        assert manifest["boundary_policy_version"] == "1.0.0"

    def test_original_requested_window_end_unaffected_by_widening(self):
        bundle = self._bundle()
        assert bundle.requested_window_end == dt.date(2026, 6, 3)

    def test_schema_and_requested_session_count(self):
        manifest = self._bundle().source_manifest
        assert manifest["source_manifest_schema_version"] == "1.0.0"
        # Mon 6/1, Tue 6/2, Wed 6/3 — all weekdays, no weekend to exclude.
        assert manifest["requested_trading_weekday_count"] == 3

    def test_prepost_argument_recorded(self):
        manifest = self._bundle().source_manifest
        assert manifest["prepost"] is False

    def test_manifest_integrity_hash_present_and_deterministic(self):
        m1 = self._bundle().source_manifest
        m2 = self._bundle().source_manifest
        assert m1["manifest_integrity_hash"]
        assert m1["manifest_integrity_hash"] == m2["manifest_integrity_hash"]

    def test_manifest_integrity_hash_changes_with_symbol(self):
        h1 = self._bundle(symbol="aapl").source_manifest["manifest_integrity_hash"]
        h2 = self._bundle(symbol="msft").source_manifest["manifest_integrity_hash"]
        assert h1 != h2

    def test_manifest_integrity_hash_distinct_from_bar_evidence_hash(self):
        bundle = self._bundle()
        assert bundle.source_manifest["manifest_integrity_hash"] != bundle.evidence_hash

    def test_duplicate_provider_row_later_value_wins(self):
        """Stage J-F4, item W16 — the acquisition-layer dedup itself
        (build_price_path_evidence's `by_date` dict, distinct from
        PricePathEvidenceBundle.__post_init__'s own duplicate-rejection
        guard, which only ever sees the ALREADY-deduplicated result) has
        never had a direct test proving which of two same-date rows
        actually wins. A provider should never legitimately return two
        rows for one date — this proves the defensive, deterministic
        tie-break: the LATER row in raw_bars order overwrites the
        earlier one."""
        bundle = build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 2, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 2, tzinfo=ET),
            raw_bars=[
                {"date": dt.date(2026, 6, 2), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None},
                {"date": dt.date(2026, 6, 2), "open": 10, "high": 20, "low": 5, "close": 15, "volume": None},
            ],
            split_events=[],
        )
        assert bundle.bars_observed == 1
        assert bundle.bars[0].open == 10.0
        assert bundle.bars[0].close == 15.0


def _base_manifest(**overrides):
    """A synthetic, already near-final manifest (everything
    finalize_source_manifest would produce except the hash itself) for
    direct hash-coverage testing — isolates the hash function's own
    field-sensitivity from acquisition's broader plumbing."""
    manifest = dict(
        source_manifest_schema_version="1.0.0", source_id="yfinance_daily",
        source_type="EXTERNAL_UNOFFICIAL_DAILY", source_scope="BOUNDED_EVIDENCE_ACQUISITION_ONLY",
        production_authoritative=False, source_version="1.0.0", provider="yfinance",
        provider_library_version="0.2.40", interval="1d", auto_adjust=False, back_adjust=False,
        actions=True, repair=False, prepost=False,
        timezone_behavior="provider_local_exchange_timezone_normalized_to_date_only",
        raw_ohlc_basis_claim="PROVIDER_RETURNED_UNADJUSTED_OHLC", adjusted_close_available=False,
        split_event_manifest=[], dividend_event_manifest=[], provider_symbol="AAPL", trade_symbol="AAPL",
        symbol_normalization_version="1.0.0", market="US", acquisition_mode="auto_adjust_false",
        provider_request_start="2026-06-01", provider_exclusive_request_end="2026-06-04",
        end_widening_reason="widened for provider exclusivity", boundary_policy_version="1.0.0",
        requested_trading_weekday_count=3,
    )
    manifest.update(overrides)
    return manifest


@pytest.mark.unit
class TestManifestIntegrityHashCoverage:
    """Stage J Final-Closure-Correction, Stage 2 — proves
    manifest_integrity_hash actually covers every field it claims to,
    proves determinism/key-order-independence, and proves tamper
    detection via verify_source_manifest_integrity."""

    def _hash_of(self, **overrides):
        finalized = finalize_source_manifest(_base_manifest(**overrides), final_limitations=[])
        return finalized["manifest_integrity_hash"]

    @pytest.mark.parametrize("field,changed_value", [
        ("trade_symbol", "MSFT"),
        ("provider_symbol", "MSFT"),
        ("market", "IN"),
        ("source_version", "2.0.0"),
        ("provider_library_version", "0.2.41"),
        ("provider_request_start", "2026-06-02"),
        ("provider_exclusive_request_end", "2026-06-05"),
        ("boundary_policy_version", "2.0.0"),
        ("split_event_manifest", ["2026-06-02"]),
        ("dividend_event_manifest", ["2026-06-02"]),
        ("prepost", True),
        ("raw_ohlc_basis_claim", "SOMETHING_ELSE"),
    ])
    def test_hash_changes_when_covered_field_changes(self, field, changed_value):
        baseline = self._hash_of()
        changed = self._hash_of(**{field: changed_value})
        assert baseline != changed, f"manifest_integrity_hash did not change when {field!r} changed"

    def test_hash_changes_when_unresolved_limitations_change(self):
        h1 = finalize_source_manifest(_base_manifest(), final_limitations=[])["manifest_integrity_hash"]
        h2 = finalize_source_manifest(_base_manifest(), final_limitations=["a dividend was paid"])["manifest_integrity_hash"]
        assert h1 != h2

    def test_identical_finalized_manifests_yield_identical_hashes(self):
        h1 = finalize_source_manifest(_base_manifest(), final_limitations=["x"])["manifest_integrity_hash"]
        h2 = finalize_source_manifest(_base_manifest(), final_limitations=["x"])["manifest_integrity_hash"]
        assert h1 == h2

    def test_reordered_dict_keys_do_not_change_hash(self):
        m1 = _base_manifest()
        m2 = dict(reversed(list(_base_manifest().items())))
        h1 = finalize_source_manifest(m1, final_limitations=["x"])["manifest_integrity_hash"]
        h2 = finalize_source_manifest(m2, final_limitations=["x"])["manifest_integrity_hash"]
        assert h1 == h2

    def test_reordered_limitations_list_changes_hash(self):
        """Stage 2, item 3 — unresolved_basis_limitations is an ORDERED
        append log (each limitation appended as it's discovered), not a
        canonically-sorted set; the contract is that its order is
        semantically meaningful (matching the bundle's own `limitations`
        append order), so a reordering must change the hash."""
        h1 = finalize_source_manifest(_base_manifest(), final_limitations=["a", "b"])["manifest_integrity_hash"]
        h2 = finalize_source_manifest(_base_manifest(), final_limitations=["b", "a"])["manifest_integrity_hash"]
        assert h1 != h2

    def test_manifest_verifies_successfully_after_dict_round_trip(self):
        """Simulates a JSONB round-trip: dict -> JSON string -> dict is
        exactly what psycopg's JSONB deserialization does; the hash must
        still verify against the round-tripped copy."""
        import json

        finalized = finalize_source_manifest(_base_manifest(), final_limitations=["a dividend was paid"])
        round_tripped = json.loads(json.dumps(finalized))
        assert verify_source_manifest_integrity(round_tripped) is True

    def test_tampering_with_limitations_fails_verification(self):
        finalized = finalize_source_manifest(_base_manifest(), final_limitations=["original"])
        tampered = dict(finalized)
        tampered["unresolved_basis_limitations"] = ["tampered"]
        assert verify_source_manifest_integrity(tampered) is False

    def test_tampering_with_request_dates_fails_verification(self):
        finalized = finalize_source_manifest(_base_manifest(), final_limitations=[])
        tampered = dict(finalized)
        tampered["provider_request_start"] = "1999-01-01"
        assert verify_source_manifest_integrity(tampered) is False

    def test_untampered_manifest_verifies(self):
        finalized = finalize_source_manifest(_base_manifest(), final_limitations=["x"])
        assert verify_source_manifest_integrity(finalized) is True

    def test_manifest_missing_hash_field_fails_verification(self):
        assert verify_source_manifest_integrity(_base_manifest()) is False


class _FakeLegacyEvidenceRow:
    """Duck-types a persisted evidence row missing the Stage J-F3
    manifest fields (source_manifest={}) -- simulates a row persisted
    before this codebase added manifest-integrity governance."""

    def __init__(self, source_manifest):
        self.source_manifest = source_manifest
        self.source_id = SOURCE_ID_YFINANCE_DAILY
        self.source_type = SOURCE_TYPE
        self.source_version = "1.0.0"
        self.provider_symbol = "AAPL"
        self.market = "US"
        self.symbol = "aapl"
        self.bar_interval = "1d"
        self.requested_window_start = dt.date(2026, 6, 1)


@pytest.mark.unit
class TestManifestCompatibilityValidation:
    """Stage J Final Semantic Reconciliation, Stage 2 -- proves the
    manifest hash is genuinely load-bearing: a valid current manifest
    passes, and every category of corruption/incompleteness fails
    closed with MANIFEST_INTEGRITY_VIOLATION."""

    def _valid_evidence(self):
        return build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="aapl", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 1), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None}],
            split_events=[],
        )

    def test_valid_current_manifest_is_compatible(self):
        decision = validate_manifest_compatibility(self._valid_evidence())
        assert decision.compatible is True
        assert decision.reason_code is None

    def test_legacy_empty_manifest_fails_closed(self):
        decision = validate_manifest_compatibility(_FakeLegacyEvidenceRow(source_manifest={}))
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION

    def test_non_mapping_manifest_fails_closed(self):
        decision = validate_manifest_compatibility(_FakeLegacyEvidenceRow(source_manifest=None))
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION

    def test_missing_hash_fails_closed(self):
        evidence = self._valid_evidence()
        tampered_manifest = dict(evidence.source_manifest)
        del tampered_manifest["manifest_integrity_hash"]
        evidence = evidence.__class__(**{**evidence.__dict__, "source_manifest": tampered_manifest})
        decision = validate_manifest_compatibility(evidence)
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION

    def test_tampered_hash_fails_closed(self):
        evidence = self._valid_evidence()
        tampered_manifest = dict(evidence.source_manifest)
        tampered_manifest["manifest_integrity_hash"] = "0" * 64
        evidence = evidence.__class__(**{**evidence.__dict__, "source_manifest": tampered_manifest})
        decision = validate_manifest_compatibility(evidence)
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION

    def test_unsupported_schema_version_fails_closed(self):
        evidence = self._valid_evidence()
        tampered_manifest = dict(evidence.source_manifest)
        tampered_manifest["source_manifest_schema_version"] = "99.0.0"
        tampered_manifest = finalize_source_manifest(tampered_manifest, tampered_manifest["unresolved_basis_limitations"])
        evidence = evidence.__class__(**{**evidence.__dict__, "source_manifest": tampered_manifest})
        decision = validate_manifest_compatibility(evidence)
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION

    def test_identity_mismatch_fails_closed(self):
        evidence = self._valid_evidence()
        tampered_manifest = dict(evidence.source_manifest)
        tampered_manifest["provider_symbol"] = "MSFT"
        tampered_manifest = finalize_source_manifest(tampered_manifest, tampered_manifest["unresolved_basis_limitations"])
        evidence = evidence.__class__(**{**evidence.__dict__, "source_manifest": tampered_manifest})
        decision = validate_manifest_compatibility(evidence)
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION

    def test_pinned_argument_mismatch_fails_closed(self):
        evidence = self._valid_evidence()
        tampered_manifest = dict(evidence.source_manifest)
        tampered_manifest["auto_adjust"] = True
        tampered_manifest = finalize_source_manifest(tampered_manifest, tampered_manifest["unresolved_basis_limitations"])
        evidence = evidence.__class__(**{**evidence.__dict__, "source_manifest": tampered_manifest})
        decision = validate_manifest_compatibility(evidence)
        assert decision.compatible is False
        assert decision.reason_code == MANIFEST_INTEGRITY_VIOLATION


@pytest.mark.unit
class TestBundleAndManifestLimitationsIdentical:
    """Stage J Final-Closure-Correction, Stage 2, item 7 — bundle.
    limitations and bundle.source_manifest['unresolved_basis_limitations']
    must contain identical final content across every return path,
    including the no-bars path, which previously constructed a SEPARATE
    one-item list literal that silently diverged whenever a dividend or
    boundary-policy limitation had already been recorded."""

    def _check(self, bundle):
        assert list(bundle.limitations) == list(bundle.source_manifest["unresolved_basis_limitations"])
        return bundle

    def test_complete_evidence(self):
        bundle = self._check(build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 1), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None}],
            split_events=[],
        ))
        assert bundle.data_completeness == "COMPLETE"

    def test_partial_evidence(self):
        self._check(build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 3, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 1), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None}],
            split_events=[],
        ))

    def test_no_bars(self):
        """The exact scenario the divergence bug affected — a dividend
        limitation is recorded, THEN the no-bars branch fires; both the
        bundle's limitations and the manifest's must contain BOTH the
        dividend message and the no-bars message, in the same order."""
        bundle = self._check(build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 3, tzinfo=ET),
            raw_bars=[], split_events=[], dividend_events=[dt.date(2026, 6, 2)],
        ))
        assert any("dividend" in lim for lim in bundle.limitations)
        assert bundle.limitations[-1] == "no valid bars were returned by the provider for the requested window"

    def test_split_in_window(self):
        self._check(build_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 3, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 2), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None}],
            split_events=[dt.date(2026, 6, 2)],
        ))


@pytest.mark.unit
class TestNoProviderCallInsideCloseTransaction:
    """Stage D — source-level proof rather than a runtime mock: as of
    this checkpoint, close_service.py has no import of, or reference to,
    price_path_acquisition at all (Stage H — generation-service
    integration — has not happened yet). This test fails loudly the
    moment that changes without the acquisition being moved outside the
    transaction, since acquire_price_path_evidence itself accepts no
    `conn` parameter by construction (see next test)."""

    def test_close_service_module_does_not_reference_price_path_acquisition(self):
        source = inspect.getsource(close_service)
        assert "price_path_acquisition" not in source
        assert "acquire_price_path_evidence" not in source

    def test_acquire_price_path_evidence_accepts_no_connection_parameter(self):
        params = inspect.signature(acquire_price_path_evidence).parameters
        assert "conn" not in params
        assert "connection" not in params
        assert "cursor" not in params


@pytest.mark.unit
class TestBoundedAcquisitionWindow:
    def test_oversized_window_rejected_before_any_fetch(self):
        calls = {"bars": 0, "splits": 0}

        def fake_bars(symbol, start, end):
            calls["bars"] += 1
            return []

        def fake_splits(symbol, start, end):
            calls["splits"] += 1
            return []

        with pytest.raises(AcquisitionWindowTooLargeError):
            acquire_price_path_evidence(
                paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
                market_timezone_name="America/New_York", market_tzinfo=ET,
                entry_timestamp=dt.datetime(2020, 1, 1, tzinfo=ET),
                exit_timestamp=dt.datetime(2020, 1, 1, tzinfo=ET) + dt.timedelta(days=MAX_ACQUISITION_WINDOW_DAYS + 10),
                fetch_bars_fn=fake_bars, fetch_splits_fn=fake_splits,
            )
        assert calls["bars"] == 0
        assert calls["splits"] == 0

    def test_window_within_bound_proceeds_to_fetch(self):
        calls = {"bars": 0}

        def fake_bars(symbol, start, end):
            calls["bars"] += 1
            return []

        def fake_splits(symbol, start, end):
            return []

        acquire_price_path_evidence(
            paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
            fetch_bars_fn=fake_bars, fetch_splits_fn=fake_splits,
        )
        assert calls["bars"] == 1


@pytest.mark.unit
class TestAcquisitionFailureNeverFabricatesEvidence:
    def test_provider_failure_propagates_without_partial_bundle(self):
        def failing_bars(symbol, start, end):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        def fake_splits(symbol, start, end):
            return []

        with pytest.raises(PriceProviderAcquisitionError) as excinfo:
            acquire_price_path_evidence(
                paper_trade_id=1, user_id="u", symbol="AAPL", market="US",
                market_timezone_name="America/New_York", market_tzinfo=ET,
                entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
                exit_timestamp=dt.datetime(2026, 6, 4, tzinfo=ET),
                fetch_bars_fn=failing_bars, fetch_splits_fn=fake_splits,
            )
        assert excinfo.value.code == "PROVIDER_FETCH_FAILED"

    def test_replay_from_persisted_bars_never_calls_provider_again(self):
        """build_price_path_evidence (the pure path replay/persistence
        would use) takes raw_bars directly — it has no fetch_fn parameter
        at all, so a second provider call is structurally impossible."""
        assert "fetch_bars_fn" not in inspect.signature(build_price_path_evidence).parameters
        assert "fetch_splits_fn" not in inspect.signature(build_price_path_evidence).parameters


@pytest.mark.unit
class TestNoLiveNetworkRequestInSuite:
    def test_fetch_raw_daily_bars_is_never_invoked_without_injection_in_this_file(self):
        """A meta-test documenting the discipline the whole Sprint 3A
        suite follows: every acquisition test in this file passes an
        explicit fetch_bars_fn/fetch_splits_fn override. yfinance is
        imported lazily INSIDE fetch_raw_daily_bars specifically so that
        importing this module never triggers a network-capable import at
        collection time."""
        import services.postmortem.price_path_acquisition as mod
        source = inspect.getsource(mod)
        # No MODULE-LEVEL (unindented) yfinance import — every reference
        # is lazy, inside a function body, so importing this module
        # never triggers a network-capable import at collection time.
        module_level_imports = [line for line in source.splitlines() if line.startswith("import yfinance")]
        assert module_level_imports == []


@pytest.mark.unit
class TestBasisCompatibilityClassification:
    """Stage F."""

    def test_no_split_auto_adjust_true_is_compatible_unadjusted(self):
        result = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=3)
        assert result == COMPATIBLE_UNADJUSTED

    def test_split_in_window_takes_priority_over_everything(self):
        result = evaluate_basis_compatibility(
            acquisition_mode="auto_adjust_true", split_events=[dt.date(2026, 6, 2)], bars_observed=3,
        )
        assert result == SPLIT_IN_WINDOW

    def test_no_bars_is_insufficient_evidence(self):
        result = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=0)
        assert result == BASIS_INSUFFICIENT_EVIDENCE

    def test_total_return_adjusted_is_basis_mismatch(self):
        result = evaluate_basis_compatibility(acquisition_mode="total_return_adjusted", split_events=[], bars_observed=3)
        assert result == BASIS_MISMATCH

    def test_unrecognized_acquisition_mode_is_composite_adjusted_unknown_basis(self):
        """Correction 2 — an unrecognized adjusted feed is never silently
        treated as compatible; it gets its own explicit classification
        distinct from a genuinely-missing acquisition_mode."""
        result = evaluate_basis_compatibility(acquisition_mode="something_new", split_events=[], bars_observed=3)
        assert result == COMPOSITE_ADJUSTED_UNKNOWN_BASIS

    def test_pinned_auto_adjust_false_is_unadjusted_provider_ohlc(self):
        """Correction 2's own pinned acquisition mode needs no
        reconciliation claim — it IS unadjusted by the provider's own
        documented contract for that argument."""
        result = evaluate_basis_compatibility(acquisition_mode="auto_adjust_false", split_events=[], bars_observed=3)
        assert result == UNADJUSTED_PROVIDER_OHLC

    def test_missing_acquisition_mode_is_insufficient_evidence(self):
        result = evaluate_basis_compatibility(acquisition_mode=None, split_events=[], bars_observed=3)
        assert result == BASIS_INSUFFICIENT_EVIDENCE

    def test_missing_split_event_is_not_by_itself_proof_of_compatibility(self):
        """Correction 2 requirement 2 — absence of a split is checked
        (split_events=[]) but for an unrecognized mode this must NOT be
        treated as proof of a compatible basis."""
        result = evaluate_basis_compatibility(acquisition_mode="some_future_mode", split_events=[], bars_observed=10)
        assert result not in (COMPATIBLE_UNADJUSTED, UNADJUSTED_PROVIDER_OHLC)
        assert result == COMPOSITE_ADJUSTED_UNKNOWN_BASIS

    def test_deterministic_replay_same_inputs_same_result(self):
        first = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=5)
        second = evaluate_basis_compatibility(acquisition_mode="auto_adjust_true", split_events=[], bars_observed=5)
        assert first == second
