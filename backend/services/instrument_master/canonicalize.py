"""
Deterministic canonicalization and hashing for the offline instrument
master (Phase C0).

Rules (per the caller's determinism requirements):
  - No call to the current clock anywhere in this module. Every
    timestamp appearing in canonical content is supplied explicitly by
    the caller (the CLI's --generated-at argument), never computed here.
  - Stable ordering: primarily by ISIN (empty/None ISIN sorts after all
    non-empty ISINs, using a fixed sentinel), secondarily by symbol.
  - Stable null representation: Python `None` -> JSON `null`, always.
  - Stable enum serialization: enum `.value` strings only, never
    `repr()`/member names.
  - UTF-8, sorted object keys, fixed separators, trailing newline.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from .schema import InstrumentMasterRecord

# Sentinel used only for *sort key* purposes so that records with no ISIN
# sort deterministically after every record that has one. Never written
# into canonical output itself (the actual `isin` field stays `None`).
_NO_ISIN_SORT_KEY = "￿￿￿￿"


def _record_sort_key(record: InstrumentMasterRecord) -> tuple[str, str]:
    isin_key = record.isin if record.isin else _NO_ISIN_SORT_KEY
    return (isin_key, record.symbol)


def sort_records(
    records: list[InstrumentMasterRecord],
) -> list[InstrumentMasterRecord]:
    return sorted(records, key=_record_sort_key)


def _default_json(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)!r} is not canonically serializable")


def _record_to_canonical_dict(record: InstrumentMasterRecord) -> dict:
    raw = asdict(record)
    # asdict() already recurses through nested dataclasses (SymbolHistoryEntry,
    # SeriesHistoryEntry) into plain dicts and leaves Enum members as Enum
    # instances (dataclasses.asdict does not convert Enum values) — normalize
    # those explicitly so json.dumps never has to guess.
    def _normalize(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, tuple):
            return [_normalize(v) for v in value]
        if isinstance(value, list):
            return [_normalize(v) for v in value]
        if isinstance(value, dict):
            return {k: _normalize(v) for k, v in value.items()}
        return value

    return {k: _normalize(v) for k, v in raw.items()}


def canonical_json_bytes(obj: Any) -> bytes:
    """Deterministic JSON serialization: sorted keys, fixed separators,
    UTF-8, trailing newline. Safe to hash directly."""
    text = json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_default_json,
    )
    return (text + "\n").encode("utf-8")


def build_canonical_snapshot(
    *,
    schema_version: int,
    generated_at: str,
    run_id: str,
    source_identifier: str,
    source_publication_date: str | None,
    input_sha256: str,
    records: list[InstrumentMasterRecord],
) -> dict:
    """Build the canonical (hashable, deterministic) snapshot dict.

    `generated_at` and `run_id` must be supplied by the caller — this
    function never reads the wall clock. Runtime/memory measurements are
    intentionally excluded from this dict; they belong in a separate
    benchmark envelope (manifest.py) that is never part of the hashed
    canonical content.
    """
    ordered = sort_records(records)
    return {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "run_id": run_id,
        "source": {
            "source_identifier": source_identifier,
            "source_publication_date": source_publication_date,
            "input_sha256": input_sha256,
        },
        "record_count": len(ordered),
        "records": [_record_to_canonical_dict(r) for r in ordered],
    }


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
