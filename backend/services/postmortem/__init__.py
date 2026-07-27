"""
Trade Postmortem Engine.

Phase 1 (current): deterministic closed-trade analytics only — outcome
classification, realized P&L, holding duration, exit-mechanism mapping,
evidence-completeness labeling. No AI narrative, no causal "why" attribution,
no database persistence. See services/postmortem/deterministic.py.

Later phases (not implemented here): immutable entry/exit evidence capture,
rules-based attribution, AI-narrated summaries, day-level pattern analysis,
report persistence/versioning — each requires its own explicitly approved
sprint per the Trade Postmortem Engine program plan.
"""
