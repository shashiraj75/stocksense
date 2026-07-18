"""
Run manifest builder for the offline instrument-master CLI (Phase C0).

The manifest is split into two parts, deliberately:
  - `canonical`: everything that must be byte-identical across two runs
    with identical inputs/arguments (record stats, reason codes, hashes).
  - `benchmark_envelope`: wall-clock timing and memory measurements,
    which are expected to vary run-to-run and must never be hashed into
    or affect the canonical-content hash.
"""
from __future__ import annotations

from .canonicalize import canonical_json_bytes, sha256_hex


def build_canonical_manifest(
    *,
    schema_version: int,
    generated_at: str,
    run_id: str,
    input_file: str,
    input_sha256: str,
    max_records: int,
    fixture_mode: bool,
    stats: dict,
    canonical_output_sha256: str,
) -> dict:
    """The part of the manifest that must be byte-identical across two
    runs with identical arguments and input bytes."""
    return {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "run_id": run_id,
        "input_file": input_file,
        "input_sha256": input_sha256,
        "max_records": max_records,
        "fixture_mode": fixture_mode,
        "total_rows": stats["total_rows"],
        "accepted_records": stats["accepted_records"],
        "rejected_records": stats["rejected_records"],
        "ambiguous_records": stats["ambiguous_records"],
        "duplicate_isin_detections": stats["duplicate_isin_detections"],
        "duplicate_symbol_detections": stats["duplicate_symbol_detections"],
        "reason_code_counts": stats["reason_code_counts"],
        "canonical_output_sha256": canonical_output_sha256,
    }


def canonical_manifest_bytes(canonical_manifest: dict) -> bytes:
    return canonical_json_bytes(canonical_manifest)


def canonical_manifest_hash(canonical_manifest: dict) -> str:
    return sha256_hex(canonical_manifest_bytes(canonical_manifest))


def build_benchmark_envelope(
    *,
    parse_seconds: float,
    normalize_seconds: float,
    total_seconds: float,
    peak_memory_bytes: int | None,
) -> dict:
    """Never included in the canonical manifest hash — timing/memory are
    expected to vary run-to-run even with identical inputs."""
    return {
        "parse_seconds": parse_seconds,
        "normalize_seconds": normalize_seconds,
        "total_seconds": total_seconds,
        "peak_memory_bytes": peak_memory_bytes,
    }


def build_safety_proof(
    *,
    network_disabled: bool,
    db_disabled: bool,
    env_file_loaded: bool,
    output_dir_outside_repos: bool,
) -> dict:
    return {
        "network_disabled": network_disabled,
        "db_disabled": db_disabled,
        "application_env_file_loaded": env_file_loaded,
        "output_dir_outside_repos": output_dir_outside_repos,
    }
