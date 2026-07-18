"""
Phase C0-A — determinism tests for canonical serialization/hashing.
"""
import os

from services.instrument_master.canonicalize import (
    build_canonical_snapshot,
    canonical_json_bytes,
    sha256_hex,
)
from services.instrument_master.manifest import (
    build_canonical_manifest,
    canonical_manifest_bytes,
)
from services.instrument_master.parser import build_records, parse_source_csv

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "instrument_master"
)


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def _build_snapshot(schema_version=1, generated_at="2026-07-19T00:00:00Z", run_id="det-run-1"):
    parsed = parse_source_csv(_read("fixture_25.csv"), source_identifier="fixture_25")
    records, stats = build_records(
        parsed,
        schema_version=schema_version,
        source_identifier="fixture_25",
        source_publication_date="2026-01-01",
    )
    snapshot = build_canonical_snapshot(
        schema_version=schema_version,
        generated_at=generated_at,
        run_id=run_id,
        source_identifier="fixture_25",
        source_publication_date="2026-01-01",
        input_sha256="deadbeef",
        records=records,
    )
    return snapshot, stats


class TestDeterminism:
    def test_repeated_normalization_is_byte_identical(self):
        snap1, _ = _build_snapshot()
        snap2, _ = _build_snapshot()
        assert canonical_json_bytes(snap1) == canonical_json_bytes(snap2)

    def test_record_order_is_stable_and_isin_first(self):
        snap, _ = _build_snapshot()
        isins = [r["isin"] for r in snap["records"]]
        non_null = [i for i in isins if i is not None]
        assert non_null == sorted(non_null)
        # The one missing-ISIN record sorts after every real ISIN.
        assert isins[-1] is None or None not in isins[:-1]

    def test_stable_hashes_across_repeated_builds(self):
        snap1, _ = _build_snapshot()
        snap2, _ = _build_snapshot()
        hash1 = sha256_hex(canonical_json_bytes(snap1))
        hash2 = sha256_hex(canonical_json_bytes(snap2))
        assert hash1 == hash2

    def test_changed_schema_version_changes_hash(self):
        snap_v1, _ = _build_snapshot(schema_version=1)
        snap_v2, _ = _build_snapshot(schema_version=2)
        hash_v1 = sha256_hex(canonical_json_bytes(snap_v1))
        hash_v2 = sha256_hex(canonical_json_bytes(snap_v2))
        assert hash_v1 != hash_v2

    def test_explicit_timestamp_controls_output_not_wall_clock(self):
        snap_a, _ = _build_snapshot(generated_at="2020-01-01T00:00:00Z")
        snap_b, _ = _build_snapshot(generated_at="2099-01-01T00:00:00Z")
        assert snap_a["generated_at"] == "2020-01-01T00:00:00Z"
        assert snap_b["generated_at"] == "2099-01-01T00:00:00Z"
        assert canonical_json_bytes(snap_a) != canonical_json_bytes(snap_b)

    def test_canonical_json_ends_with_trailing_newline(self):
        snap, _ = _build_snapshot()
        assert canonical_json_bytes(snap).endswith(b"\n")

    def test_canonical_manifest_excludes_benchmark_timing(self):
        snap, stats = _build_snapshot()
        manifest = build_canonical_manifest(
            schema_version=1,
            generated_at="2026-07-19T00:00:00Z",
            run_id="det-run-1",
            input_file="fixture_25.csv",
            input_sha256="deadbeef",
            max_records=25,
            fixture_mode=True,
            stats=stats,
            canonical_output_sha256=sha256_hex(canonical_json_bytes(snap)),
        )
        assert "parse_seconds" not in manifest
        assert "peak_memory_bytes" not in manifest
        # Two manifests built with identical inputs but representing two
        # different (hypothetical) wall-clock runs must hash identically,
        # since no timing field ever enters the canonical manifest.
        manifest2 = build_canonical_manifest(
            schema_version=1,
            generated_at="2026-07-19T00:00:00Z",
            run_id="det-run-1",
            input_file="fixture_25.csv",
            input_sha256="deadbeef",
            max_records=25,
            fixture_mode=True,
            stats=stats,
            canonical_output_sha256=sha256_hex(canonical_json_bytes(snap)),
        )
        assert canonical_manifest_bytes(manifest) == canonical_manifest_bytes(manifest2)
