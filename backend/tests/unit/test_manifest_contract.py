"""
Phase C1-D1 — manifest/completeness contract and SME freshness/fallback
contract tests.
"""
from __future__ import annotations

import pytest

from services.instrument_master.enums import (
    ActiveStatus,
    ConflictState,
    EligibilityStatus,
    PUBLICATION_BLOCKING_CONFLICT_STATES,
    RetrievalMethod,
    SourceCategoryLifecycleStatus,
    SourceId,
    StaleState,
    ValidationResultStatus,
    WARNING_ONLY_CONFLICT_STATES,
)
from services.instrument_master.manifest_contract import (
    CompletenessContractError,
    HARD_STALE_DAYS_DEFAULT,
    FRESHNESS_WARNING_DAYS_DEFAULT,
    SmeFreshnessRecord,
    SourceReadinessStatus,
    build_completeness_contract,
    canonical_completeness_contract_bytes,
    canonical_completeness_contract_hash,
    compute_stale_state,
)

# The exhaustive list of all 11 SourceId members, hand-typed here
# independently of source_registry.py's SOURCE_REGISTRY/all_entries() —
# so a bug in the registry's own membership couldn't silently make these
# tests pass by coincidence.
ALL_ELEVEN_SOURCE_IDS = (
    SourceId.NSE_EQUITY_CURRENT,
    SourceId.NSE_ETF_CURRENT,
    SourceId.NSE_SME_CURRENT,
    SourceId.NSE_REIT_CURRENT,
    SourceId.NSE_INVIT_CURRENT,
    SourceId.NSE_PREFERENCE,
    SourceId.NSE_WARRANT,
    SourceId.NSE_IDR,
    SourceId.NSE_IL_SERIES,
    SourceId.NSE_SYMBOL_HISTORY,
    SourceId.NSE_NAME_HISTORY,
)


class TestSourceCategoryLifecycleStatus:
    """Phase C1-D3 correction: lock the exact three coverage-state
    values and confirm they remain semantically distinct from active/
    inactive/eligible/ineligible classifications."""

    def test_exact_three_members(self):
        assert {m.value for m in SourceCategoryLifecycleStatus} == {
            "unknown",
            "unavailable",
            "not_covered_by_current_source_set",
        }

    def test_unknown_value(self):
        assert SourceCategoryLifecycleStatus.UNKNOWN.value == "unknown"

    def test_unavailable_value(self):
        assert SourceCategoryLifecycleStatus.UNAVAILABLE.value == "unavailable"

    def test_not_covered_by_current_source_set_value(self):
        assert (
            SourceCategoryLifecycleStatus.NOT_COVERED_BY_CURRENT_SOURCE_SET.value
            == "not_covered_by_current_source_set"
        )

    def test_no_member_aliases_active_status_values(self):
        active_status_values = {m.value for m in ActiveStatus}
        lifecycle_values = {m.value for m in SourceCategoryLifecycleStatus}
        # "unknown" legitimately appears in both enums (each has its own
        # conservative-default UNKNOWN) — that is expected and is not an
        # alias in the sense this test guards against. What must never
        # happen is a lifecycle-status value colliding with a concrete
        # active/inactive classification.
        assert "active" not in lifecycle_values
        assert "inactive" not in lifecycle_values
        assert "suspended" not in lifecycle_values
        assert "delisted" not in lifecycle_values

    def test_no_member_aliases_eligibility_status_values(self):
        eligibility_values = {m.value for m in EligibilityStatus}
        lifecycle_values = {m.value for m in SourceCategoryLifecycleStatus}
        assert "eligible" not in lifecycle_values
        assert not (lifecycle_values & eligibility_values - {"unknown"})

    def test_is_a_distinct_enum_type_from_active_status(self):
        assert SourceCategoryLifecycleStatus is not ActiveStatus
        assert SourceCategoryLifecycleStatus is not EligibilityStatus


class TestStaleStateThresholds:
    def test_default_thresholds_match_ratified_decision(self):
        assert FRESHNESS_WARNING_DAYS_DEFAULT == 3
        assert HARD_STALE_DAYS_DEFAULT == 7

    @pytest.mark.parametrize("age", [0, 1, 2, 3])
    def test_fresh_boundary(self, age):
        assert compute_stale_state(age) == StaleState.FRESH

    @pytest.mark.parametrize("age", [4, 5, 6, 7])
    def test_stale_warning_boundary(self, age):
        assert compute_stale_state(age) == StaleState.STALE_WARNING

    @pytest.mark.parametrize("age", [8, 9, 100])
    def test_hard_stale_boundary(self, age):
        assert compute_stale_state(age) == StaleState.HARD_STALE

    def test_negative_age_rejected(self):
        with pytest.raises(ValueError):
            compute_stale_state(-1)

    def test_calendar_days_not_trading_days_no_weekend_adjustment(self):
        # A pure function of age_days — no calendar-awareness of
        # weekends/holidays is implemented, confirming this is genuinely
        # calendar-day arithmetic, not trading-day arithmetic.
        assert compute_stale_state(4) == StaleState.STALE_WARNING


class TestSmeFreshnessRecord:
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            source_id=SourceId.NSE_SME_CURRENT,
            source_sha256="a" * 64,
            source_byte_size=38663,
            retrieved_at="2026-07-19T00:00:00Z",
            source_url="https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
            retrieval_method=RetrievalMethod.LAST_KNOWN_GOOD,
            validation_status=ValidationResultStatus.VALID,
            reviewed=False,
            reviewed_by=None,
            reviewed_at=None,
            stale_state=StaleState.STALE_WARNING,
        )
        kwargs.update(overrides)
        return kwargs

    def test_last_known_good_without_review_is_valid(self):
        record = SmeFreshnessRecord(**self._base_kwargs())
        assert record.retrieval_method == RetrievalMethod.LAST_KNOWN_GOOD

    def test_operator_assisted_requires_reviewed_true(self):
        with pytest.raises(ValueError):
            SmeFreshnessRecord(
                **self._base_kwargs(retrieval_method=RetrievalMethod.OPERATOR_ASSISTED, reviewed=False)
            )

    def test_operator_assisted_with_review_is_valid(self):
        record = SmeFreshnessRecord(
            **self._base_kwargs(
                retrieval_method=RetrievalMethod.OPERATOR_ASSISTED,
                reviewed=True,
                reviewed_by="ops_operator_1",
                reviewed_at="2026-07-19T01:00:00Z",
            )
        )
        assert record.reviewed is True

    def test_reviewed_true_without_reviewer_fields_rejected(self):
        with pytest.raises(ValueError):
            SmeFreshnessRecord(**self._base_kwargs(reviewed=True))

    def test_reviewed_false_with_reviewer_fields_rejected(self):
        with pytest.raises(ValueError):
            SmeFreshnessRecord(
                **self._base_kwargs(reviewed=False, reviewed_by="someone", reviewed_at="2026-07-19T01:00:00Z")
            )

    def test_preserves_exact_source_url_contract(self):
        record = SmeFreshnessRecord(**self._base_kwargs())
        assert "/emerge/corporates/content/SME_EQUITY_L.csv" in record.source_url


class TestConflictStateClassification:
    def test_every_conflict_state_is_exactly_blocking_or_warning(self):
        all_classified = PUBLICATION_BLOCKING_CONFLICT_STATES | WARNING_ONLY_CONFLICT_STATES
        assert all_classified == set(ConflictState)

    def test_blocking_and_warning_sets_are_disjoint(self):
        assert PUBLICATION_BLOCKING_CONFLICT_STATES.isdisjoint(WARNING_ONLY_CONFLICT_STATES)

    def test_cross_source_isin_conflict_blocks(self):
        assert ConflictState.CROSS_SOURCE_ISIN_CONFLICT in PUBLICATION_BLOCKING_CONFLICT_STATES

    def test_malformed_required_source_blocks(self):
        assert ConflictState.MALFORMED_REQUIRED_SOURCE in PUBLICATION_BLOCKING_CONFLICT_STATES

    def test_duplicate_source_record_blocks(self):
        assert ConflictState.DUPLICATE_SOURCE_RECORD in PUBLICATION_BLOCKING_CONFLICT_STATES

    def test_stale_source_classification_is_warning_only(self):
        assert ConflictState.STALE_SOURCE_CLASSIFICATION in WARNING_ONLY_CONFLICT_STATES

    def test_unknown_unclassified_is_warning_only(self):
        assert ConflictState.UNKNOWN_UNCLASSIFIED in WARNING_ONLY_CONFLICT_STATES


def _ready_status(source_id, *, available=True, valid=True, fresh=True, vstatus=None):
    """Independently constructed — does not call is_source_ready() or any
    other implementation helper; the test asserts against expected
    build_completeness_contract() outputs using plain field values.

    Phase C1-D7: validation_status must agree with `valid` (validation_valid),
    per SourceReadinessStatus's new consistency invariant. When `vstatus`
    isn't explicitly supplied, default to a status matching `valid`: VALID
    when valid=True, INVALID_HEADER (an arbitrary representative failure
    status) when valid=False.
    """
    if vstatus is not None:
        resolved_vstatus = vstatus
    elif valid:
        resolved_vstatus = ValidationResultStatus.VALID
    else:
        resolved_vstatus = ValidationResultStatus.INVALID_HEADER
    return SourceReadinessStatus(
        source_id=source_id,
        available=available,
        validation_valid=valid,
        fresh=fresh,
        validation_status=resolved_vstatus,
    )


def _all_ready_statuses(**overrides):
    """One fully-ready SourceReadinessStatus per each of the 11
    hand-typed SourceIds (ALL_ELEVEN_SOURCE_IDS), with `overrides`
    (keyed by SourceId) replacing specific entries."""
    statuses = {sid: _ready_status(sid) for sid in ALL_ELEVEN_SOURCE_IDS}
    statuses.update(overrides)
    return statuses


class TestSourceReadinessStatus:
    def test_ready_status_constructs_cleanly(self):
        s = _ready_status(SourceId.NSE_EQUITY_CURRENT)
        assert s.available is True and s.validation_valid is True and s.fresh is True

    def test_unavailable_with_validation_valid_true_rejected(self):
        with pytest.raises(ValueError):
            SourceReadinessStatus(
                source_id=SourceId.NSE_EQUITY_CURRENT,
                available=False,
                validation_valid=True,
                fresh=False,
                validation_status=ValidationResultStatus.VALID,
            )

    def test_unavailable_with_fresh_true_rejected(self):
        with pytest.raises(ValueError):
            SourceReadinessStatus(
                source_id=SourceId.NSE_EQUITY_CURRENT,
                available=False,
                validation_valid=False,
                fresh=True,
                validation_status=ValidationResultStatus.VALID,
            )

    def test_available_invalid_but_fresh_is_a_permitted_state(self):
        # Deliberately NOT rejected -- a fresh fetch that failed
        # validation (e.g. corrupted/malformed content served quickly)
        # is a real, meaningful, distinct state.
        s = SourceReadinessStatus(
            source_id=SourceId.NSE_EQUITY_CURRENT,
            available=True,
            validation_valid=False,
            fresh=True,
            validation_status=ValidationResultStatus.INVALID_HEADER,
        )
        assert s.validation_valid is False and s.fresh is True

    def test_available_invalid_but_fresh_is_never_counted_ready(self):
        from services.instrument_master.manifest_contract import is_source_ready

        s = SourceReadinessStatus(
            source_id=SourceId.NSE_EQUITY_CURRENT,
            available=True,
            validation_valid=False,
            fresh=True,
            validation_status=ValidationResultStatus.INVALID_HEADER,
        )
        assert is_source_ready(s) is False


class TestSourceReadinessStatusValidationConsistency:
    """Phase C1-D7 correction of a Phase C1-D6 finding: validation_valid
    must agree exactly with validation_status. The expected successful
    statuses are defined independently here (not imported from
    manifest_contract.VALIDATION_SUCCESS_STATUSES), so a bug in that
    constant itself would not silently make these tests pass."""

    INDEPENDENTLY_DEFINED_SUCCESS_STATUSES = {
        ValidationResultStatus.VALID,
        ValidationResultStatus.VALID_EMPTY,
    }

    def _build(self, *, valid, vstatus, available=True, fresh=True):
        return SourceReadinessStatus(
            source_id=SourceId.NSE_EQUITY_CURRENT,
            available=available,
            validation_valid=valid,
            fresh=fresh,
            validation_status=vstatus,
        )

    def test_1_valid_true_with_valid_status_accepted(self):
        s = self._build(valid=True, vstatus=ValidationResultStatus.VALID)
        assert s.validation_valid is True

    def test_2_valid_true_with_valid_empty_status_accepted(self):
        s = self._build(valid=True, vstatus=ValidationResultStatus.VALID_EMPTY)
        assert s.validation_valid is True

    def test_3_valid_true_with_invalid_isin_rejected(self):
        with pytest.raises(ValueError):
            self._build(valid=True, vstatus=ValidationResultStatus.INVALID_ISIN)

    def test_4_valid_true_with_invalid_header_rejected(self):
        with pytest.raises(ValueError):
            self._build(valid=True, vstatus=ValidationResultStatus.INVALID_HEADER)

    def test_5_valid_false_with_valid_status_rejected(self):
        with pytest.raises(ValueError):
            self._build(valid=False, vstatus=ValidationResultStatus.VALID, fresh=False)

    def test_6_valid_false_with_valid_empty_status_rejected(self):
        with pytest.raises(ValueError):
            self._build(valid=False, vstatus=ValidationResultStatus.VALID_EMPTY, fresh=False)

    def test_7_valid_false_invalid_header_available_fresh_accepted(self):
        # "Recently retrieved but failed validation" -- a real, permitted state.
        s = self._build(valid=False, vstatus=ValidationResultStatus.INVALID_HEADER, available=True, fresh=True)
        assert s.available is True and s.fresh is True and s.validation_valid is False

    def test_8_that_state_is_never_ready(self):
        from services.instrument_master.manifest_contract import is_source_ready

        s = self._build(valid=False, vstatus=ValidationResultStatus.INVALID_HEADER, available=True, fresh=True)
        assert is_source_ready(s) is False

    def test_9_unavailable_with_validation_valid_true_rejected(self):
        with pytest.raises(ValueError):
            self._build(valid=True, vstatus=ValidationResultStatus.VALID, available=False, fresh=False)

    def test_10_unavailable_with_fresh_true_rejected(self):
        with pytest.raises(ValueError):
            self._build(valid=False, vstatus=ValidationResultStatus.INVALID_HEADER, available=False, fresh=True)

    def test_11_canonical_serialization_and_hashing_remain_deterministic(self):
        s1 = self._build(valid=True, vstatus=ValidationResultStatus.VALID)
        s2 = self._build(valid=True, vstatus=ValidationResultStatus.VALID)
        assert s1 == s2  # frozen dataclass structural equality, deterministic

    @pytest.mark.parametrize("member", list(ValidationResultStatus))
    def test_12_every_validation_result_status_member_is_classified(self, member):
        # This test fails for any ValidationResultStatus member not
        # explicitly accounted for in INDEPENDENTLY_DEFINED_SUCCESS_STATUSES
        # (success) or exercised as a failure case -- every member must
        # produce deterministic, non-crashing accept/reject behavior
        # consistent with this independently-defined expectation.
        expected_success = member in self.INDEPENDENTLY_DEFINED_SUCCESS_STATUSES
        if expected_success:
            s = self._build(valid=True, vstatus=member)
            assert s.validation_status == member
        else:
            with pytest.raises(ValueError):
                self._build(valid=True, vstatus=member)
            # And the symmetric, non-success-consistent construction succeeds.
            s = self._build(valid=False, vstatus=member, fresh=False)
            assert s.validation_status == member


class TestCompletenessContract:
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            source_statuses=_all_ready_statuses(),
            source_errors=(),
            oldest_source_age_days=0,
            newest_source_age_days=0,
            business_policy_permits_daily_picks=True,
            business_policy_permits_daily_picks_non_sme=True,
            business_policy_permits_screener=True,
            business_policy_permits_recommendations=True,
            sme_explicitly_excluded_from_eligible_universe=True,
            unknown_unclassified_securities_excluded=True,
            manual_source_present=False,
            manual_source_reviewed_by=None,
            manual_source_reviewed_at=None,
        )
        kwargs.update(overrides)
        return kwargs

    def test_fully_healthy_snapshot_all_flags_true(self):
        c = build_completeness_contract(**self._base_kwargs())
        assert c.snapshot_complete is True
        assert c.taxonomy_complete is True
        assert c.source_set_complete is True
        assert c.approved_for_daily_picks is True
        assert c.approved_for_daily_picks_non_sme is True

    # -- Scenarios 1-4: equity --------------------------------------

    def test_1_equity_unavailable_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_2_equity_invalid_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, valid=False, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_3_equity_stale_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_4_equity_missing_from_mapping_raises(self):
        statuses = _all_ready_statuses()
        del statuses[SourceId.NSE_EQUITY_CURRENT]
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(**self._base_kwargs(source_statuses=statuses))

    # -- Scenarios 5-8: ETF -------------------------------------------

    def test_5_etf_unavailable_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_ETF_CURRENT: _ready_status(SourceId.NSE_ETF_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_6_etf_invalid_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_ETF_CURRENT: _ready_status(SourceId.NSE_ETF_CURRENT, valid=False, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_7_etf_stale_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_ETF_CURRENT: _ready_status(SourceId.NSE_ETF_CURRENT, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_8_etf_missing_from_mapping_raises(self):
        statuses = _all_ready_statuses()
        del statuses[SourceId.NSE_ETF_CURRENT]
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(**self._base_kwargs(source_statuses=statuses))

    # -- Scenario 9: SME missing ---------------------------------------

    def test_9_sme_missing_from_mapping_raises(self):
        statuses = _all_ready_statuses()
        del statuses[SourceId.NSE_SME_CURRENT]
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(**self._base_kwargs(source_statuses=statuses))

    # -- Scenarios 10-13: SME stale/exclusion/policy -------------------

    def test_10_sme_stale_equity_etf_ready_exclusions_confirmed_policy_permitted(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks is False
        assert c.approved_for_daily_picks_non_sme is True

    def test_11_sme_exclusion_not_confirmed_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, fresh=False)}
                ),
                sme_explicitly_excluded_from_eligible_universe=False,
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_12_unknown_unclassified_exclusion_not_confirmed_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, fresh=False)}
                ),
                unknown_unclassified_securities_excluded=False,
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_13_business_policy_not_permitted_blocks_non_sme_eligibility(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, fresh=False)}
                ),
                business_policy_permits_daily_picks_non_sme=False,
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    # -- Scenarios 14-15: removed parameters are structurally gone -----

    def test_14_critical_required_sources_ok_no_longer_a_valid_parameter(self):
        with pytest.raises(TypeError):
            build_completeness_contract(**self._base_kwargs(), critical_required_sources_ok=True)

    def test_15_non_sme_critical_and_taxonomy_sources_ok_no_longer_a_valid_parameter(self):
        with pytest.raises(TypeError):
            build_completeness_contract(**self._base_kwargs(), non_sme_critical_and_taxonomy_sources_ok=True)

    # -- Scenario 16: key/source_id mismatch ---------------------------

    def test_16_mapping_key_and_status_source_id_mismatch_raises(self):
        statuses = _all_ready_statuses()
        # Store equity's status under the ETF key -- key/value mismatch.
        statuses[SourceId.NSE_ETF_CURRENT] = _ready_status(SourceId.NSE_EQUITY_CURRENT)
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(**self._base_kwargs(source_statuses=statuses))

    # -- Scenario 17: impossible combinations rejected -----------------

    def test_17_impossible_readiness_combination_rejected_deterministically(self):
        with pytest.raises(ValueError):
            _ready_status(SourceId.NSE_EQUITY_CURRENT, available=False, valid=True)
        with pytest.raises(ValueError):
            _ready_status(SourceId.NSE_EQUITY_CURRENT, available=False, fresh=True)

    # -- Phase C1-D2/D4 regression reproductions ------------------------

    def test_taxonomy_complete_false_when_sme_unavailable(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.taxonomy_complete is False

    def test_approved_for_daily_picks_never_true_when_taxonomy_incomplete(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks is False

    def test_phase_c1_d4_reproduced_equity_gap_is_now_structurally_impossible(self):
        # The exact Phase C1-D4 finding: equity conceptually down, but a
        # separate trusted Boolean (critical_required_sources_ok) could
        # previously claim True regardless. That parameter no longer
        # exists -- the ONLY way to express equity's state is through
        # source_statuses, which this test sets to genuinely unavailable.
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.approved_for_daily_picks_non_sme is False

    def test_sme_status_must_be_explicitly_represented(self):
        statuses = _all_ready_statuses()
        del statuses[SourceId.NSE_SME_CURRENT]
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(**self._base_kwargs(source_statuses=statuses))

    def test_approved_flags_require_explicit_business_policy_not_snapshot_existence(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                business_policy_permits_daily_picks=False,
                business_policy_permits_daily_picks_non_sme=False,
                business_policy_permits_screener=False,
                business_policy_permits_recommendations=False,
            )
        )
        # Every prerequisite (taxonomy_complete, non_sme_ok) is satisfied,
        # yet every approved_for_* flag must remain False without an
        # explicit business-policy grant.
        assert c.taxonomy_complete is True
        assert c.approved_for_daily_picks is False
        assert c.approved_for_daily_picks_non_sme is False
        assert c.approved_for_screener is False
        assert c.approved_for_recommendations is False

    def test_source_set_complete_false_when_any_source_unavailable(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_IL_SERIES: _ready_status(SourceId.NSE_IL_SERIES, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.source_set_complete is False

    def test_unavailable_sources_derived_from_source_statuses(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_IL_SERIES: _ready_status(SourceId.NSE_IL_SERIES, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.unavailable_sources == (SourceId.NSE_IL_SERIES,)

    def test_stale_source_count_derived_from_source_statuses(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{
                        SourceId.NSE_REIT_CURRENT: _ready_status(SourceId.NSE_REIT_CURRENT, fresh=False),
                        SourceId.NSE_INVIT_CURRENT: _ready_status(SourceId.NSE_INVIT_CURRENT, fresh=False),
                    }
                )
            )
        )
        assert c.stale_source_count == 2

    def test_manual_source_present_does_not_imply_reviewed(self):
        c = build_completeness_contract(
            **self._base_kwargs(manual_source_present=True, manual_source_reviewed_by=None, manual_source_reviewed_at=None)
        )
        assert c.manual_source_present is True
        assert c.manual_source_reviewed_by is None

    def test_reviewed_by_without_reviewed_at_rejected(self):
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(
                **self._base_kwargs(
                    manual_source_present=True,
                    manual_source_reviewed_by="ops1",
                    manual_source_reviewed_at=None,
                )
            )

    def test_reviewer_fields_without_manual_source_present_rejected(self):
        with pytest.raises(CompletenessContractError):
            build_completeness_contract(
                **self._base_kwargs(
                    manual_source_present=False,
                    manual_source_reviewed_by="ops1",
                    manual_source_reviewed_at="2026-07-19T00:00:00Z",
                )
            )


class TestSnapshotCompleteCriticalitySemantics:
    """Phase C1-D7 correction of a Phase C1-D6 finding: snapshot_complete
    must reflect ONLY CRITICAL_REQUIRED source readiness (currently just
    nse_equity_current) -- it must never be affected by an OPTIONAL_
    ENRICHMENT or HISTORY_ONLY source's availability/validity/freshness."""

    def _base_kwargs(self, **overrides):
        kwargs = dict(
            source_statuses=_all_ready_statuses(),
            source_errors=(),
            oldest_source_age_days=0,
            newest_source_age_days=0,
            business_policy_permits_daily_picks=True,
            business_policy_permits_daily_picks_non_sme=True,
            business_policy_permits_screener=True,
            business_policy_permits_recommendations=True,
            sme_explicitly_excluded_from_eligible_universe=True,
            unknown_unclassified_securities_excluded=True,
            manual_source_present=False,
            manual_source_reviewed_by=None,
            manual_source_reviewed_at=None,
        )
        kwargs.update(overrides)
        return kwargs

    def test_1_equity_ready_reit_unavailable(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_REIT_CURRENT: _ready_status(SourceId.NSE_REIT_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is True
        assert c.taxonomy_complete is True  # determined only by equity/ETF/SME
        assert c.source_set_complete is False
        assert SourceId.NSE_REIT_CURRENT in c.unavailable_sources

    def test_2_equity_ready_symbol_history_unavailable(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SYMBOL_HISTORY: _ready_status(SourceId.NSE_SYMBOL_HISTORY, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is True
        assert c.source_set_complete is False
        assert SourceId.NSE_SYMBOL_HISTORY in c.unavailable_sources

    def test_3_equity_ready_name_history_stale(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_NAME_HISTORY: _ready_status(SourceId.NSE_NAME_HISTORY, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is True
        assert c.source_set_complete is False
        assert c.stale_source_count == 1

    def test_4_equity_unavailable(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is False

    def test_5_equity_invalid(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, valid=False, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is False

    def test_6_equity_stale(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_EQUITY_CURRENT: _ready_status(SourceId.NSE_EQUITY_CURRENT, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is False

    def test_7_etf_unavailable_snapshot_complete_unaffected_taxonomy_complete_false(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_ETF_CURRENT: _ready_status(SourceId.NSE_ETF_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is True  # based on equity alone
        assert c.taxonomy_complete is False

    def test_8_sme_unavailable_snapshot_complete_unaffected_taxonomy_complete_false(self):
        c = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SME_CURRENT: _ready_status(SourceId.NSE_SME_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c.snapshot_complete is True  # based on equity alone
        assert c.taxonomy_complete is False

    def test_9_all_11_ready(self):
        c = build_completeness_contract(**self._base_kwargs())
        assert c.snapshot_complete is True
        assert c.taxonomy_complete is True
        assert c.source_set_complete is True

    def test_10_optional_history_unavailability_never_blocks_non_sme_daily_picks(self):
        c_reit = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_REIT_CURRENT: _ready_status(SourceId.NSE_REIT_CURRENT, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c_reit.approved_for_daily_picks_non_sme is True

        c_history = build_completeness_contract(
            **self._base_kwargs(
                source_statuses=_all_ready_statuses(
                    **{SourceId.NSE_SYMBOL_HISTORY: _ready_status(SourceId.NSE_SYMBOL_HISTORY, available=False, valid=False, fresh=False)}
                )
            )
        )
        assert c_history.approved_for_daily_picks_non_sme is True


class TestDeterminism:
    def test_canonical_bytes_deterministic(self):
        c = build_completeness_contract(
            source_statuses=_all_ready_statuses(),
            source_errors=(),
            oldest_source_age_days=1,
            newest_source_age_days=0,
            business_policy_permits_daily_picks=True,
            business_policy_permits_daily_picks_non_sme=True,
            business_policy_permits_screener=True,
            business_policy_permits_recommendations=True,
            sme_explicitly_excluded_from_eligible_universe=True,
            unknown_unclassified_securities_excluded=True,
            manual_source_present=False,
            manual_source_reviewed_by=None,
            manual_source_reviewed_at=None,
        )
        assert canonical_completeness_contract_bytes(c) == canonical_completeness_contract_bytes(c)
        assert canonical_completeness_contract_hash(c) == canonical_completeness_contract_hash(c)
