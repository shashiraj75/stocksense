"""
Trade Postmortem Engine, Sprint 2 — unit tests for
services.postmortem.report_store: idempotent versioned persistence and
user-scoped lookup.
"""
import datetime as dt
import json

import pytest

from services.postmortem.report_store import compute_evidence_hash, get_current_report, persist_report


class _FakeConn:
    def __init__(self):
        self.rows: dict[int, tuple] = {}
        self.next_id = 1

    def execute(self, sql, params):
        stripped = sql.strip()
        if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
            (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
             ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings, supersedes_id) = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = None
                    return self
            new_id = self.next_id
            self.next_id += 1
            row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                   bundle_v, ev_hash, status,
                   json.loads(structured), json.loads(ev_items), json.loads(claims),
                   json.loads(manifest), json.loads(gaps), json.loads(warnings),
                   dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc), supersedes_id)
            self.rows[new_id] = row
            self._pending = row
            return self
        if stripped.startswith("SELECT") and "WHERE paper_trade_id = %s AND user_id = %s" in sql:
            paper_trade_id, user_id, schema_v, calc_v, rules_v = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.rows.values():
                if (row[1], row[6], row[7], row[8]) == key and row[2] == user_id:
                    self._pending = row
                    return self
            self._pending = None
            return self
        if stripped.startswith("SELECT") and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
            paper_trade_id, schema_v, calc_v, rules_v = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = row
                    return self
            self._pending = None
            return self
        raise AssertionError(f"unexpected SQL: {sql!r}")

    def fetchone(self):
        return self._pending


def _persist(conn, **overrides):
    kwargs = dict(
        paper_trade_id=1, user_id="user-aaa", market="US",
        report_trading_date=dt.date(2026, 6, 1), market_timezone="America/New_York",
        report_schema_version="1.0.0", calculation_version="2.0.0", attribution_rules_version="2.0.0",
        evidence_bundle_version="1.0.0", status="COMPLETE",
        structured_report={"a": 1}, evidence_items=[{"id": "ev1"}], claims=[{"id": "cl1"}],
        source_manifest={"has_entry_snapshot": True}, evidence_gaps=[], warnings=[],
    )
    kwargs.update(overrides)
    return persist_report(conn, **kwargs)


@pytest.mark.unit
class TestPersistReport:
    def test_first_call_creates(self):
        conn = _FakeConn()
        report, created = _persist(conn)
        assert created is True
        assert report.status == "COMPLETE"
        assert report.evidence_hash

    def test_second_call_same_versions_is_idempotent_no_duplicate_row(self):
        conn = _FakeConn()
        first, created1 = _persist(conn)
        second, created2 = _persist(conn)
        assert created1 is True
        assert created2 is False
        assert first.id == second.id
        assert len(conn.rows) == 1

    def test_new_calculation_version_inserts_a_new_row(self):
        """A genuinely new rules/calculation version must never overwrite
        the prior row — it inserts a new one under its own version key."""
        conn = _FakeConn()
        first, _ = _persist(conn)
        second, created = _persist(conn, calculation_version="3.0.0")
        assert created is True
        assert first.id != second.id
        assert len(conn.rows) == 2

    def test_different_trade_ids_never_collide(self):
        conn = _FakeConn()
        a, _ = _persist(conn, paper_trade_id=1)
        b, _ = _persist(conn, paper_trade_id=2)
        assert a.id != b.id

    def test_evidence_hash_deterministic_for_identical_evidence(self):
        h1 = compute_evidence_hash([{"id": "ev1"}], [{"id": "cl1"}])
        h2 = compute_evidence_hash([{"id": "ev1"}], [{"id": "cl1"}])
        assert h1 == h2

    def test_evidence_hash_differs_for_different_evidence(self):
        h1 = compute_evidence_hash([{"id": "ev1"}], [{"id": "cl1"}])
        h2 = compute_evidence_hash([{"id": "ev2"}], [{"id": "cl1"}])
        assert h1 != h2

    def test_json_fields_survive_serialization_round_trip(self):
        conn = _FakeConn()
        report, _ = _persist(conn, structured_report={"nested": {"value": 1}}, evidence_gaps=["gap one"])
        assert report.structured_report == {"nested": {"value": 1}}
        assert report.evidence_gaps == ["gap one"]


@pytest.mark.unit
class TestGetCurrentReport:
    def test_returns_report_for_owning_user(self):
        conn = _FakeConn()
        persisted, _ = _persist(conn)
        found = get_current_report(
            conn, paper_trade_id=1, user_id="user-aaa",
            report_schema_version="1.0.0", calculation_version="2.0.0", attribution_rules_version="2.0.0",
        )
        assert found is not None
        assert found.id == persisted.id

    def test_returns_none_for_non_owning_user(self):
        conn = _FakeConn()
        _persist(conn)
        found = get_current_report(
            conn, paper_trade_id=1, user_id="attacker",
            report_schema_version="1.0.0", calculation_version="2.0.0", attribution_rules_version="2.0.0",
        )
        assert found is None

    def test_returns_none_when_no_report_exists(self):
        conn = _FakeConn()
        found = get_current_report(
            conn, paper_trade_id=999, user_id="user-aaa",
            report_schema_version="1.0.0", calculation_version="2.0.0", attribution_rules_version="2.0.0",
        )
        assert found is None


@pytest.mark.unit
class TestReportColumnsOrderingWaveC:
    """WC-K-14 guard — _REPORT_COLUMNS and _row_to_report's tuple
    unpack must stay in lockstep. A silent drift between them (adding a
    column to one but not the other) is exactly the defect class real
    PostgreSQL caught during Wave C: six hand-written fake-connection
    fixtures across the test suite hardcoded a 19-column row shape and
    broke the instant a 20th (generated_at) column was added here."""

    def test_report_columns_ends_with_generated_at_then_supersedes_report_id(self):
        from services.postmortem.report_store import _REPORT_COLUMNS
        columns = [c.strip() for c in _REPORT_COLUMNS.split(",")]
        assert columns[-2:] == ["generated_at", "supersedes_report_id"]

    def test_row_to_report_maps_generated_at_and_supersedes_id_by_position(self):
        from services.postmortem.report_store import _REPORT_COLUMNS, _row_to_report
        columns = [c.strip() for c in _REPORT_COLUMNS.split(",")]
        assert len(columns) == 20, (
            f"expected exactly 20 persisted columns, found {len(columns)} — "
            "if this genuinely changes, _row_to_report's unpack and every "
            "hand-written fake-connection test fixture must be updated together"
        )
        stamp = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
        row = tuple(range(1, 19)) + (stamp, 999)  # generated_at, supersedes_report_id
        report = _row_to_report(row)
        assert report.generated_at == stamp
        assert report.supersedes_report_id == 999


class _FakeConnCapturingParams:
    """Minimal fake conn that captures the INSERT params so we can
    inspect the exact JSON strings persist_report would have sent,
    without needing a real database."""

    def __init__(self):
        self.last_params = None

    def execute(self, sql, params):
        self.last_params = params
        return self

    def fetchone(self):
        return None  # no existing row; forces the caller down the "conflict, re-select" path is irrelevant here


@pytest.mark.unit
class TestCanonicalJsonDefaultRejectsUnsupportedObjects:
    """Real defect found by real-PostgreSQL CI: a governed price-path
    EvidenceItem/PostmortemClaim dataclass instance reaching
    persist_report's json.dumps(..., default=str) call was silently
    stringified into an opaque repr instead of raising — the caller-side
    fix lives in current_report_generation.build_current_report_payload,
    but persist_report itself must also refuse to silently accept an
    unsupported object for claims/evidence_items, as defense in depth."""

    def _base_kwargs(self, **overrides):
        kwargs = dict(
            paper_trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 1), market_timezone="America/New_York",
            report_schema_version="1.2.0", calculation_version="c", attribution_rules_version="a",
            evidence_bundle_version="e", status="COMPLETE",
            structured_report={"a": 1}, source_manifest={}, evidence_gaps=[], warnings=[],
        )
        kwargs.update(overrides)
        return kwargs

    def test_dataclass_evidence_item_is_converted_via_asdict_defense_in_depth(self):
        """A dataclass instance reaching persist_report should no longer
        happen from the real pipeline (canonicalized upstream in
        current_report_generation.build_current_report_payload), but as
        defense-in-depth this encoder still converts one via
        dataclasses.asdict rather than silently stringifying it — proven
        by the INSERT's json.dumps succeeding (reaching the
        RuntimeError from the fake conn's always-empty fetchone, not a
        TypeError)."""
        import dataclasses as dc

        @dc.dataclass
        class _FakeEvidenceItem:
            evidence_id: str = "EV-1"

        conn = _FakeConnCapturingParams()
        with pytest.raises(RuntimeError, match="no existing row found"):
            persist_report(conn, evidence_items=[_FakeEvidenceItem()], claims=[], **self._base_kwargs())

    def test_unsupported_non_dataclass_object_raises_instead_of_being_stringified(self):
        """The genuine defect class this guards against: an arbitrary
        object that is NOT a dataclass, Enum, datetime or date — the
        only case json.dumps(..., default=str) would previously have
        silently stringified into an opaque repr."""
        class _NotJsonSafe:
            def __repr__(self):
                return "<_NotJsonSafe opaque repr>"

        conn = _FakeConnCapturingParams()
        with pytest.raises(TypeError, match="unsupported"):
            persist_report(conn, evidence_items=[_NotJsonSafe()], claims=[], **self._base_kwargs())

    def test_plain_dict_evidence_items_and_claims_serialize_without_raising(self):
        conn = _FakeConnCapturingParams()
        # Real INSERT would return a row; this fake returns None, which
        # persist_report's own SELECT-fallback would then also see None
        # for — we only care that the json.dumps() calls inside the
        # INSERT execute() call didn't raise, which happens before the
        # RETURNING row is even read.
        with pytest.raises(RuntimeError, match="no existing row found"):
            persist_report(
                conn,
                evidence_items=[{"evidence_id": "EV-1"}], claims=[{"claim_id": "CLM-1"}],
                **self._base_kwargs(),
            )
        # Reaching the "no existing row found" RuntimeError proves the
        # INSERT's json.dumps(canonicalize_report_json(...)) calls
        # completed successfully for plain dicts.


@pytest.mark.unit
class TestCanonicalizeReportJson:
    """Package A1 — canonicalize_report_json is the ONE authoritative
    path for every persisted report JSON value, used identically by
    both compute_evidence_hash and persist_report."""

    def test_scalars_pass_through_unchanged(self):
        from services.postmortem.report_store import canonicalize_report_json

        assert canonicalize_report_json("s") == "s"
        assert canonicalize_report_json(1) == 1
        assert canonicalize_report_json(1.5) == 1.5
        assert canonicalize_report_json(True) is True
        assert canonicalize_report_json(None) is None

    def test_nested_dict_and_list_recurse(self):
        from services.postmortem.report_store import canonicalize_report_json

        assert canonicalize_report_json({"a": [1, {"b": 2}]}) == {"a": [1, {"b": 2}]}

    def test_enum_converts_to_its_value(self):
        import enum
        from services.postmortem.report_store import canonicalize_report_json

        class _E(enum.Enum):
            X = "GOVERNED_X"

        assert canonicalize_report_json(_E.X) == "GOVERNED_X"
        assert canonicalize_report_json({"k": _E.X}) == {"k": "GOVERNED_X"}

    def test_datetime_and_date_convert_to_isoformat(self):
        from services.postmortem.report_store import canonicalize_report_json

        d = dt.date(2026, 6, 1)
        t = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone.utc)
        assert canonicalize_report_json(d) == d.isoformat()
        assert canonicalize_report_json(t) == t.isoformat()

    def test_dataclass_converts_via_asdict_recursively(self):
        import dataclasses as dc
        from services.postmortem.report_store import canonicalize_report_json

        @dc.dataclass
        class _Inner:
            value: str

        @dc.dataclass
        class _Outer:
            inner: _Inner
            when: dt.datetime

        t = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        result = canonicalize_report_json(_Outer(inner=_Inner(value="x"), when=t))
        assert result == {"inner": {"value": "x"}, "when": t.isoformat()}

    def test_equivalent_dataclass_and_dict_produce_identical_canonical_form(self):
        import dataclasses as dc
        from services.postmortem.report_store import canonicalize_report_json

        @dc.dataclass
        class _Item:
            evidence_id: str
            category: str

        as_dataclass = canonicalize_report_json(_Item(evidence_id="EV-1", category="c"))
        as_dict = canonicalize_report_json({"evidence_id": "EV-1", "category": "c"})
        assert as_dataclass == as_dict

    def test_non_string_dict_key_raises(self):
        from services.postmortem.report_store import canonicalize_report_json

        with pytest.raises(TypeError, match="must be strings"):
            canonicalize_report_json({1: "value"})

    def test_unsupported_object_raises(self):
        from services.postmortem.report_store import canonicalize_report_json

        class _NotJsonSafe:
            pass

        with pytest.raises(TypeError, match="unsupported"):
            canonicalize_report_json(_NotJsonSafe())

        with pytest.raises(TypeError, match="unsupported"):
            canonicalize_report_json({"nested": [1, _NotJsonSafe()]})


@pytest.mark.unit
class TestEvidenceHashUsesSharedCanonicalization:
    def test_equivalent_dataclass_and_dict_evidence_produce_the_same_hash(self):
        import dataclasses as dc

        @dc.dataclass
        class _Item:
            evidence_id: str

        h_dataclass = compute_evidence_hash([_Item(evidence_id="EV-1")], [])
        h_dict = compute_evidence_hash([{"evidence_id": "EV-1"}], [])
        assert h_dataclass == h_dict

    def test_nested_enum_and_datetime_canonicalize_consistently_in_the_hash(self):
        import enum

        class _E(enum.Enum):
            X = "V"

        t = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        h1 = compute_evidence_hash([{"status": _E.X, "ts": t}], [])
        h2 = compute_evidence_hash([{"status": "V", "ts": t.isoformat()}], [])
        assert h1 == h2

    def test_persisted_evidence_hash_matches_recomputation_over_canonical_content(self):
        conn = _FakeConn()
        report, _ = _persist(conn, evidence_items=[{"id": "ev1"}], claims=[{"id": "cl1"}])
        recomputed = compute_evidence_hash(report.evidence_items, report.claims)
        assert report.evidence_hash == recomputed

    def test_unsupported_nested_object_in_evidence_items_raises_before_insert(self):
        class _NotJsonSafe:
            pass

        conn = _FakeConn()
        with pytest.raises(TypeError, match="unsupported"):
            _persist(conn, evidence_items=[{"bad": _NotJsonSafe()}])
        assert len(conn.rows) == 0, "no partial report row may be inserted when canonicalization fails"

    def test_unsupported_nested_object_in_claims_raises_before_insert(self):
        class _NotJsonSafe:
            pass

        conn = _FakeConn()
        with pytest.raises(TypeError, match="unsupported"):
            _persist(conn, claims=[{"bad": _NotJsonSafe()}])
        assert len(conn.rows) == 0


@pytest.mark.unit
class TestCanonicalizeReportJsonHardening:
    """Package A1 hardening items A-D."""

    def test_enum_is_checked_before_the_scalar_branch(self):
        """A governed enum inheriting from (str, Enum) must canonicalize
        to its plain .value string, never be returned as the raw Enum
        object (which isinstance(value, _JSON_SCALAR_TYPES) would
        otherwise match first, since str is a scalar type)."""
        import enum
        from services.postmortem.report_store import canonicalize_report_json

        class _StrEnum(str, enum.Enum):
            X = "GOVERNED_X"

        result = canonicalize_report_json(_StrEnum.X)
        assert result == "GOVERNED_X"
        assert type(result) is str, f"expected plain str, got {type(result).__name__}"

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_are_rejected(self, value):
        from services.postmortem.report_store import canonicalize_report_json

        with pytest.raises(TypeError, match="non-finite"):
            canonicalize_report_json(value)

    def test_non_finite_float_nested_in_a_dict_is_rejected(self):
        from services.postmortem.report_store import canonicalize_report_json

        with pytest.raises(TypeError, match="non-finite"):
            canonicalize_report_json({"value": float("nan")})

    def test_finite_float_is_accepted(self):
        from services.postmortem.report_store import canonicalize_report_json

        assert canonicalize_report_json(1.5) == 1.5

    def test_persist_report_hashes_and_persists_the_same_canonical_collections_once(self):
        """persist_report must canonicalize evidence_items/claims exactly
        once and reuse those same canonical collections for both the
        hash and the INSERT payload — proven indirectly: the persisted
        evidence_hash must equal _hash_canonical_evidence's result over
        the already-canonical persisted content (not a second, separate
        canonicalization pass that could theoretically diverge)."""
        from services.postmortem.report_store import _hash_canonical_evidence, canonicalize_report_json

        conn = _FakeConn()
        report, _ = _persist(conn, evidence_items=[{"id": "ev1"}], claims=[{"id": "cl1"}])
        expected = _hash_canonical_evidence(
            canonicalize_report_json(report.evidence_items), canonicalize_report_json(report.claims),
        )
        assert report.evidence_hash == expected


@pytest.mark.unit
class TestNoPartialWriteAcrossAllSixFields:
    """Package A1 item D — an unsupported nested value in ANY of the six
    authoritative fields must raise before the first database execute
    call, for every field, not just evidence_items/claims."""

    class _NotJsonSafe:
        pass

    @pytest.mark.parametrize("field", [
        "structured_report", "evidence_items", "claims", "source_manifest", "evidence_gaps", "warnings",
    ])
    def test_unsupported_object_in_any_field_raises_before_any_insert(self, field):
        conn = _FakeConn()
        bad_value = {"bad": self._NotJsonSafe()} if field in ("structured_report", "source_manifest") else (
            [self._NotJsonSafe()] if field in ("evidence_gaps", "warnings") else [{"bad": self._NotJsonSafe()}]
        )
        with pytest.raises(TypeError, match="unsupported"):
            _persist(conn, **{field: bad_value})
        assert len(conn.rows) == 0, f"no partial report row may be inserted when {field} fails canonicalization"
