"""
DP-025 foundation — offline, deterministic pipeline-replay tooling.

Governed by DPD-009 (DECIDED — DISCLOSURE HOLD). This package is NOT
invoked by any production code path, scheduler, or API endpoint — it is an
explicitly-run offline tool and test dependency only. See
`pipeline_replay.py`'s module docstring for the full scope statement and
side-effect prohibition list.
"""
