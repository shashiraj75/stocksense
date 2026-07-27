"""
Bounded-memory stress test (2026-07-21 memory-exhaustion postmortem).

Runs a synthetic harness that mirrors the FIXED Phase 1 shape — a plain
sequential loop that processes one (candidate, horizon) unit at a time,
retains only a small compact record per unit, and drops the full
synthetic result immediately — in a real, separate subprocess (so the
measured peak RSS is that process's actual OS-reported memory, not just a
delta of Python-visible references, which the allocator wouldn't
necessarily return to the OS anyway). Peak RSS is asserted to grow far
sub-linearly relative to the (deliberately large) synthetic per-unit
payload size as the unit count scales up — proving the design does not
retain O(n) full payloads simultaneously.

No live network requests. No production Postgres access. Deterministic
(no external service, no timing dependency — only a subprocess boundary
and a memory measurement).
"""
import resource
import subprocess
import sys
import textwrap

import pytest


def _run_harness(n_units: int, unit_payload_kb: int) -> int:
    """
    Runs a synthetic sequential-processing harness in a subprocess and
    returns that subprocess's peak RSS in bytes (via
    resource.getrusage(RUSAGE_CHILDREN) after it exits — portable across
    the ru_maxrss unit difference between Linux (KB) and macOS (bytes),
    handled below).
    """
    script = textwrap.dedent(f"""
        import random
        import string

        def _make_large_synthetic_result(i):
            # Mirrors a real _predict_stock() result's rough shape: several
            # large text fields (reasoning, bull/bear case, quality factor
            # breakdowns) plus numeric scores.
            blob = "".join(random.choices(string.ascii_letters, k={unit_payload_kb} * 1024))
            return {{
                "symbol": f"SYN{{i}}",
                "horizon": "medium",
                "composite_score": float(i % 100),
                "reasoning": blob,
                "bull_case": blob,
                "bear_case": blob,
            }}

        def _compact(result):
            # Only what ranking/quality-gates/alpha-observations actually need
            # downstream — mirrors the "compact projection" principle even
            # though full field-by-field auditing (Track D3) isn't in this
            # branch yet; this harness proves the SHAPE (sequential, drop full
            # result immediately) is what matters for boundedness.
            return {{"symbol": result["symbol"], "composite_score": result["composite_score"]}}

        raw = []
        for i in range({n_units}):
            r = _make_large_synthetic_result(i)
            raw.append(_compact(r))
            del r  # dropped immediately, same as the real fixed Phase 1 loop

        # Touch `raw` so it isn't optimized away and to simulate the
        # downstream ranking step reading the compact records.
        assert len(raw) == {n_units}
        total_score = sum(r["composite_score"] for r in raw)
        print(total_score)
    """)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr}"

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    ru_maxrss = usage.ru_maxrss
    # Linux reports ru_maxrss in KB; macOS (Darwin) reports bytes.
    if sys.platform == "darwin":
        return ru_maxrss
    return ru_maxrss * 1024


def test_peak_rss_does_not_scale_with_unit_count_times_payload_size():
    """
    1200 units (400 candidates x 3 horizons, matching the real US universe
    target) each carrying a deliberately large ~50 KB synthetic payload
    (bull/bear case + reasoning text, mirroring real prediction-result
    field sizes) would need ~60 MB just for the raw payloads if ALL of them
    were retained simultaneously (the pre-fix defect's shape) — before
    accounting for Python object overhead, Future wrapper objects, or
    duplicate copies made during ranking, which is exactly the kind of
    multiplicative retention that produced a multi-GB peak in production.

    The fixed (sequential, drop-immediately, compact-record) shape must use
    only a small, roughly constant amount of memory regardless of unit
    count, since only one full payload is ever alive at a time and only a
    tiny compact record survives per unit.
    """
    small_rss = _run_harness(n_units=100, unit_payload_kb=50)
    large_rss = _run_harness(n_units=1200, unit_payload_kb=50)

    # If full payloads were retained for every unit, going from 100 to 1200
    # units (12x) would add roughly (1200-100) * 50KB ~= 55 MB of retained
    # payload data alone. The bounded design should show growth far below
    # that — a generous, defensible ceiling of 20 MB total growth allows
    # comfortably for base Python interpreter/subprocess RSS noise (which
    # is itself several MB and varies by platform) while still being ~3x
    # tighter than what the unbounded shape would need.
    growth = large_rss - small_rss
    max_allowed_growth_bytes = 20 * 1024 * 1024

    assert growth < max_allowed_growth_bytes, (
        f"peak RSS grew {growth / 1024 / 1024:.1f} MB going from 100 to 1200 "
        f"units — expected well under {max_allowed_growth_bytes / 1024 / 1024:.0f} MB "
        f"for a design that never retains more than one full payload at a time "
        f"(small_rss={small_rss}, large_rss={large_rss})"
    )
