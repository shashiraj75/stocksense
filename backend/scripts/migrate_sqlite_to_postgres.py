"""
One-time migration: copy rows from local alpha_engine.db (SQLite) into
Supabase Postgres. Run manually, locally, with DATABASE_URL pointed at
production:

    DATABASE_URL="postgresql://..." python3 scripts/migrate_sqlite_to_postgres.py

Safe to run multiple times — outcomes uses ON CONFLICT DO NOTHING;
predictions/regime_log rows will duplicate on repeat runs (they have no
natural unique key), so only run this once per environment.
"""
import os
import sys
import sqlite3
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import postgres_store as pg  # noqa: E402

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "alpha_engine.db")


def main():
    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL not set — aborting.")
        return

    if not os.path.exists(SQLITE_PATH):
        print(f"No SQLite DB found at {SQLITE_PATH} — nothing to migrate.")
        return

    pg.init_db()

    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row

    predictions = conn.execute("SELECT * FROM predictions").fetchall()
    print(f"Migrating {len(predictions)} predictions...")
    for p in predictions:
        pg.log_prediction(
            symbol=p["symbol"], horizon=p["horizon"],
            factor_zscores={"tech": p["tech_z"], "fund": p["fund_z"],
                             "sentiment": p["sentiment_z"], "quality": p["quality_z"]},
            combined_alpha=p["combined_alpha"], meta_alpha=p["meta_alpha"],
            signal=p["signal"], price=p["price"], regime_label=p["regime_label"] or "",
        )

    outcomes = conn.execute("SELECT * FROM outcomes").fetchall()
    print(f"Migrating {len(outcomes)} outcomes...")
    for o in outcomes:
        pg.log_outcome(
            symbol=o["symbol"], horizon=o["horizon"], pred_date=o["pred_date"],
            return_1d=o["return_1d"], return_5d=o["return_5d"], return_20d=o["return_20d"],
        )

    regimes = conn.execute("SELECT * FROM regime_log").fetchall()
    print(f"Migrating {len(regimes)} regime snapshots...")
    for r in regimes:
        try:
            features = json.loads(r["features"])
        except Exception:
            features = []
        pg.log_regime(regime_id=r["regime_id"], label=r["label"], features=features)

    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
