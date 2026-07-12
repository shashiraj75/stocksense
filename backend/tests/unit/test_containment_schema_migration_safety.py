"""
Learning Alpha Engine remediation, Phase 1 — schema migration safety.

Regression proof: the Phase 1 schema additions (paper_trades provenance
columns, daily_picks_jobs containment-observability columns) are pure
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` — additive and nullable only.
No historical row of paper_trades, predictions, outcomes,
alpha_observations, factor_ic_history, or daily_picks_jobs is touched by
this remediation phase.
"""

import re

from services.postgres_store import SCHEMA_SQL

_PHASE1_MARKER = "Learning Alpha Engine remediation, Phase 1"

_NEW_PAPER_TRADES_COLUMNS = (
    "recommendation_source", "daily_pick_run_id", "daily_pick_rank",
    "recommendation_generated_at", "recommendation_reference_price",
    "recommendation_entry_low", "recommendation_entry_high",
    "recommendation_original_stop_loss", "recommendation_original_target",
    "model_version", "execution_slippage_pct", "signal_override",
    "levels_modified_after_entry",
)

_NEW_JOBS_COLUMNS = (
    "production_alpha_source", "shadow_ic_available",
    "shadow_meta_model_available", "containment_reason",
    "learning_dataset_version",
)


def test_phase1_markers_exist_in_schema():
    assert SCHEMA_SQL.count(_PHASE1_MARKER) >= 2, (
        "expected Phase 1 banners for both the paper_trades and "
        "daily_picks_jobs additions"
    )


def test_every_new_column_is_declared_as_additive_and_nullable():
    for table, columns in (
        ("paper_trades", _NEW_PAPER_TRADES_COLUMNS),
        ("daily_picks_jobs", _NEW_JOBS_COLUMNS),
    ):
        for col in columns:
            pattern = rf"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col}\b[^;]*;"
            match = re.search(pattern, SCHEMA_SQL)
            assert match, f"missing additive declaration for {table}.{col}"
            assert "NOT NULL" not in match.group(0), f"{table}.{col} must remain nullable"


def test_no_update_or_delete_statement_targets_the_new_columns():
    for col in _NEW_PAPER_TRADES_COLUMNS + _NEW_JOBS_COLUMNS:
        # An UPDATE that SETs one of these new columns would be a backfill —
        # not permitted in this remediation phase. Only edit_trade's
        # forward-looking levels_modified_after_entry write (application
        # code, not schema SQL) is allowed to set a new column, and that's
        # scoped to a single freshly-edited row via WHERE id = %s, not a
        # bulk historical UPDATE — so it must not appear in SCHEMA_SQL here.
        assert f"UPDATE paper_trades SET {col}" not in SCHEMA_SQL
        assert f"UPDATE daily_picks_jobs SET {col}" not in SCHEMA_SQL


def test_no_delete_or_drop_statements_in_schema_sql_at_all():
    assert "DROP TABLE" not in SCHEMA_SQL
    assert re.search(r"\bDELETE FROM\b", SCHEMA_SQL) is None
    assert re.search(r"\bDROP COLUMN\b", SCHEMA_SQL) is None
