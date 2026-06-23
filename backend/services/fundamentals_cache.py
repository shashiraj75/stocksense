"""
Cached fundamentals for the full NSE universe, refreshed nightly by
fundamentals_refresh.py. Backs the Multibagger Screen feature — screening
2,300+ stocks live against screener.in on every request isn't viable (too
slow, and it would hammer screener.in hard enough to risk getting our IP
blocked, which we also depend on for predictions and the Fundamentals tab).
Instead we scrape once overnight and run instant SQL filters against this
table.
"""
import os
from datetime import datetime, timezone


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def ensure_table():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_fundamentals_cache (
                symbol                   TEXT PRIMARY KEY,
                company_name             TEXT,
                sector_name              TEXT,
                industry_name            TEXT,
                is_financial             BOOLEAN NOT NULL DEFAULT FALSE,
                market_cap_cr            NUMERIC,
                pe_ratio                 NUMERIC,
                roe_pct                  NUMERIC,
                roe_5y_pct               NUMERIC,
                roce_pct                 NUMERIC,
                debt_to_equity_pct       NUMERIC,
                promoter_holding_pct     NUMERIC,
                promoter_pledge_pct      NUMERIC,
                sales_growth_3y_pct      NUMERIC,
                sales_growth_5y_pct      NUMERIC,
                profit_growth_3y_pct     NUMERIC,
                profit_growth_5y_pct     NUMERIC,
                opm_pct                  NUMERIC,
                interest_coverage_ratio  NUMERIC,
                ev_ebitda                NUMERIC,
                price_to_sales           NUMERIC,
                operating_cf_latest_cr   NUMERIC,
                updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fundamentals_cache_updated ON stock_fundamentals_cache(updated_at)")


# Maps our internal field names to the table's columns — single source of
# truth so the refresh job and the upsert statement can't drift apart.
FIELD_MAP = {
    "company_name":            "company_name",
    "sector_name":              "sector_name",
    "industry_name":            "industry_name",
    "market_cap_cr":            "market_cap_cr",
    "pe_ratio":                 "pe_ratio",
    "roe_pct":                  "roe_pct",
    "roe_5y_pct":                "roe_5y_pct",
    "roce_pct":                  "roce_pct",
    "debt_to_equity_pct":       "debt_to_equity_pct",
    "promoter_holding_pct":     "promoter_holding_pct",
    "promoter_pledge_pct":      "promoter_pledge_pct",
    "sales_growth_3y_pct":      "sales_growth_3y_pct",
    "sales_growth_5y_pct":      "sales_growth_5y_pct",
    "profit_growth_3y_pct":     "profit_growth_3y_pct",
    "profit_growth_5y_pct":     "profit_growth_5y_pct",
    "opm_pct":                   "opm_pct",
    "interest_coverage_ratio":  "interest_coverage_ratio",
    "ev_ebitda":                 "ev_ebitda",
    "price_to_sales":           "price_to_sales",
    "operating_cf_latest_cr":   "operating_cf_latest_cr",
}


def upsert(symbol: str, is_financial: bool, fields: dict):
    cols = ["symbol", "is_financial", "updated_at"] + list(FIELD_MAP.values())
    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "symbol")
    values = [symbol, is_financial, datetime.now(timezone.utc)] + [fields.get(k) for k in FIELD_MAP]

    with _conn() as conn:
        conn.execute(f"""
            INSERT INTO stock_fundamentals_cache ({", ".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT (symbol) DO UPDATE SET {update_clause}
        """, values)


def query_screen(screen: str) -> list[dict]:
    """
    Each screen is a hard AND-filter — a stock must pass every condition to
    appear, matching the "2-Screen System" design (combining loose +
    strict criteria into one screen produces zero/over-expensive results).
    """
    where, order = _SCREENS.get(screen, (None, None))
    if where is None:
        return []

    with _conn() as conn:
        rows = conn.execute(f"""
            SELECT symbol, company_name, sector_name, market_cap_cr, pe_ratio,
                   roe_pct, roe_5y_pct, roce_pct, debt_to_equity_pct,
                   promoter_holding_pct, promoter_pledge_pct,
                   sales_growth_3y_pct, sales_growth_5y_pct,
                   profit_growth_3y_pct, profit_growth_5y_pct,
                   opm_pct, interest_coverage_ratio, ev_ebitda, price_to_sales,
                   operating_cf_latest_cr, updated_at
            FROM stock_fundamentals_cache
            WHERE {where}
            ORDER BY {order}
        """).fetchall()
        cols = [
            "symbol", "company_name", "sector_name", "market_cap_cr", "pe_ratio",
            "roe_pct", "roe_5y_pct", "roce_pct", "debt_to_equity_pct",
            "promoter_holding_pct", "promoter_pledge_pct",
            "sales_growth_3y_pct", "sales_growth_5y_pct",
            "profit_growth_3y_pct", "profit_growth_5y_pct",
            "opm_pct", "interest_coverage_ratio", "ev_ebitda", "price_to_sales",
            "operating_cf_latest_cr", "updated_at",
        ]
        return [dict(zip(cols, row)) for row in rows]


def last_refreshed() -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT MAX(updated_at) FROM stock_fundamentals_cache").fetchone()
        return row[0].isoformat() if row and row[0] else None


# Quality Compounders and Multibagger Discovery don't depend on the new
# annual-P&L fields, so banks/NBFCs (where those fields are structurally
# absent — see fundamentals_refresh.py) aren't excluded from them. The
# 10-Bagger screen's OPM/Interest-Coverage checks naturally exclude
# financials since those columns are NULL for them (NULL fails any
# comparison), which is the correct behavior — those ratios aren't
# meaningful for a bank/NBFC anyway.
_SCREENS: dict[str, tuple[str, str]] = {
    "quality_compounder": (
        """
        market_cap_cr > 2000
        AND roe_5y_pct > 18
        AND roce_pct > 15
        AND debt_to_equity_pct < 50
        AND promoter_pledge_pct < 1
        AND promoter_holding_pct > 35
        AND sales_growth_5y_pct > 10
        AND profit_growth_5y_pct > 10
        AND pe_ratio < 35
        AND ev_ebitda < 20
        AND operating_cf_latest_cr > 0
        """,
        "roe_5y_pct DESC",
    ),
    "multibagger_discovery": (
        """
        market_cap_cr > 300 AND market_cap_cr < 20000
        AND sales_growth_3y_pct > 15
        AND profit_growth_3y_pct > 15
        AND roce_pct > 12
        AND debt_to_equity_pct < 100
        AND promoter_pledge_pct < 2
        AND price_to_sales < 5
        AND pe_ratio < 50
        """,
        "profit_growth_3y_pct DESC",
    ),
    "tenbagger_early": (
        """
        market_cap_cr > 300 AND market_cap_cr < 15000
        AND sales_growth_3y_pct > 20
        AND profit_growth_3y_pct > 20
        AND roce_pct > 10
        AND roe_pct > 8
        AND debt_to_equity_pct < 100
        AND interest_coverage_ratio > 2
        AND promoter_pledge_pct < 2
        AND price_to_sales < 4
        AND pe_ratio < 60
        AND opm_pct > 8
        """,
        "sales_growth_3y_pct DESC",
    ),
}
