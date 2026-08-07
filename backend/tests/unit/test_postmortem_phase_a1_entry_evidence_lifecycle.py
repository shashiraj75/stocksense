"""
Trade Postmortem Engine, Phase A1 — end-to-end regression proving captured
entry evidence survives the full lifecycle (Buy -> entry snapshot -> close
-> Postmortem report) unchanged, and is never substituted by a later or
"current" recommendation value.

Drives the SAME production functions the real Buy/close/report path uses
(no parallel fake implementation, no hand-built snapshot row):
  - `services.postmortem.entry_snapshot.build_entry_snapshot` — exactly
    what `api/routers/paper_trading.py`'s `paper_buy` calls to build the
    row it persists via `_insert_entry_snapshot` (see
    `backend/tests/integration/test_paper_trading_entry_snapshot.py`,
    which separately proves that persistence call happens atomically with
    the trade INSERT).
  - `services.postmortem.exit_snapshot.build_exit_snapshot` — what the
    close path builds (see `test_postmortem_close_service.py`).
  - `services.postmortem.deterministic.compute_postmortem` and
    `services.postmortem.generation_service.build_report_payload` /
    `generate_and_persist` — exactly what the postmortem-generation path
    (api/routers/paper_trading.py's `_generate_postmortem_for_trade`,
    which itself calls `_fetch_entry_snapshot` then these two functions)
    uses to turn a persisted entry snapshot into a report.

This intentionally exercises the service layer rather than re-faking the
HTTP/DB plumbing that `test_paper_trading_entry_snapshot.py` and
`test_paper_trading_postmortem_endpoint.py` already cover exhaustively
(atomic persistence, auth, 404/409 semantics, SQL shape). What's missing
before this file is a single test proving the VALUES survive the full
Buy -> snapshot -> close -> report chain unchanged — that's this file's
only job.
"""
import datetime as dt

import pytest

from services.postmortem.close_service import CALCULATION_VERSION
from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.entry_snapshot import (
    EntrySnapshot,
    RecommendationContext,
    VerificationLevel,
    build_entry_snapshot,
)
from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION
from services.postmortem.exit_snapshot import CloseExitMechanism, build_exit_snapshot
from services.postmortem.generation_service import build_report_payload

UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Step 1-3: Buy — build the entry snapshot through the real production
# function, the same one paper_buy() calls, with a representative set of
# Phase A1 entry-evidence fields for a DAILY_PICK-originated trade.
# ---------------------------------------------------------------------------

def _original_entry_evidence_ctx() -> RecommendationContext:
    """Fix 5 (owner-audit correction, Phase A1): this fixture is
    evidence_source RESEARCH, not DAILY_PICK — Stock Detail (RESEARCH) is
    the only live Buy path that genuinely supplies technical_signal,
    technical_rsi, technical_macd_diff, and a numeric sentiment_score today
    (see frontend/src/utils/entryEvidence.ts:buildEntryEvidenceFromPrediction).
    The current Daily Pick path (buildEntryEvidenceFromDailyPick) leaves
    technical_signal/sentiment_score null and never sends
    daily_pick_run_id/daily_pick_rank/model_version — a DAILY_PICK fixture
    carrying all of these fields would misrepresent real frontend behavior.
    See TestPhaseA1EntryEvidenceLifecycleDailyPickSparse below for a
    DAILY_PICK sub-case matching what that path actually produces."""
    return RecommendationContext(
        evidence_source="RESEARCH",
        simulated_execution_price=101.0,
        recommendation_signal="BUY",
        recommendation_generated_at=dt.datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        recommendation_reference_price=100.0,
        recommendation_entry_low=98.0,
        recommendation_entry_high=102.0,
        recommended_stop_loss=90.0,
        recommended_target_price=130.0,
        confidence_score=82.0,
        technical_signal="BUY",
        technical_rsi=58.0,
        technical_macd_diff=1.25,
        fundamental_score=71.0,
        sentiment_score=64.0,
        sentiment_label="POSITIVE",
        market_regime_trend="BULLISH",
        market_regime_score_adj=3.5,
        market_regime_reason="Broad market uptrend confirmed by regime model",
        recommendation_reasoning=["Strong RSI momentum", "Positive earnings surprise"],
        # RESEARCH-path Buys never populate these — buildEntryEvidenceFromPrediction
        # hard-codes them to null (a live Prediction response carries no
        # governed daily-pick-run/rank/model-version semantics).
        daily_pick_run_id=None,
        daily_pick_rank=None,
        model_version=None,
    )


def _build_original_snapshot(trade_id: int = 900001) -> EntrySnapshot:
    return build_entry_snapshot(
        paper_trade_id=trade_id, user_id="user-aaa", symbol="AAPL", market="US",
        ctx=_original_entry_evidence_ctx(),
        level_history_contract_version="1.0.0",
    )


def _closed_trade_record(trade_id: int = 900001) -> ClosedTradeRecord:
    return ClosedTradeRecord(
        trade_id=trade_id, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=101.0, exit_price=125.0, stop_loss=90.0, target_price=130.0,
        opened_at=dt.datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
        closed_at=dt.datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
        trade_management_mode="manual", exit_reason="MANUAL",
    )


def _build_exit_snapshot_for(trade_id: int = 900001):
    return build_exit_snapshot(
        paper_trade_id=trade_id, user_id="user-aaa", symbol="AAPL", market="US",
        exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        exit_price=125.0, exit_quantity=10,
        closed_at=dt.datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
        realized_pnl_abs=240.0, management_mode="manual",
    )


def _evidence_item(payload, name: str) -> dict:
    matches = [item for item in payload.evidence_items if item["name"] == name]
    assert len(matches) == 1, f"expected exactly one evidence_item named {name!r}, found {len(matches)}"
    return matches[0]


@pytest.mark.unit
class TestPhaseA1EntryEvidenceLifecycle:
    """Buy -> entry snapshot -> close -> Postmortem report, driven through
    the real production functions, for a FUTURE (post-Phase-A1) trade."""

    def test_full_lifecycle_report_uses_original_entry_time_values_exactly(self):
        snapshot = _build_original_snapshot()
        record = _closed_trade_record()
        exit_snapshot = _build_exit_snapshot_for()

        postmortem = compute_postmortem(record, snapshot=snapshot)
        payload = build_report_payload(postmortem, snapshot, exit_snapshot=exit_snapshot)

        # A. Postmortem uses the recommendation signal captured at entry.
        rec_signal_item = _evidence_item(payload, "recommendation_signal")
        assert rec_signal_item["value"] == "BUY"

        # B. Postmortem uses the technical signal captured at entry.
        tech_item = _evidence_item(payload, "technical_signal")
        assert tech_item["value"] == "BUY"

        # C. Postmortem uses the fundamental score captured at entry.
        fundamental_item = _evidence_item(payload, "fundamental_score")
        assert fundamental_item["value"] == 71.0

        # D. Postmortem uses the sentiment score captured at entry.
        sentiment_item = _evidence_item(payload, "sentiment_score")
        assert sentiment_item["value"] == 64.0

        # E. Point-in-time immutability: report evidence equals the
        # originally persisted entry-snapshot values exactly (not merely
        # "truthy" — exact equality against what build_entry_snapshot
        # produced at Buy time).
        assert rec_signal_item["value"] == snapshot.recommendation_signal
        assert tech_item["value"] == snapshot.technical_signal
        assert fundamental_item["value"] == snapshot.fundamental_score
        assert sentiment_item["value"] == snapshot.sentiment_score

        # G. Provenance: every entry-evidence item stays CLIENT_REPORTED,
        # never upgraded to SERVER_VERIFIED.
        for name in ("recommendation_signal", "technical_signal", "fundamental_score", "sentiment_score"):
            item = _evidence_item(payload, name)
            assert item["verification_level"] == VerificationLevel.CLIENT_REPORTED.value
            assert item["verification_level"] != "SERVER_VERIFIED"
        assert snapshot.verification_levels["recommendation_signal"] == VerificationLevel.CLIENT_REPORTED.value
        assert snapshot.verification_levels["technical_signal"] == VerificationLevel.CLIENT_REPORTED.value
        assert snapshot.verification_levels["fundamental_score"] == VerificationLevel.CLIENT_REPORTED.value
        assert snapshot.verification_levels["sentiment_score"] == VerificationLevel.CLIENT_REPORTED.value

        # Report was built from real entry evidence, so it must not be
        # LIMITED_EVIDENCE purely for lack of an entry snapshot.
        assert payload.source_manifest["has_entry_snapshot"] is True
        assert payload.source_manifest["attribution_rules_version"] == ATTRIBUTION_RULES_VERSION

    def test_report_never_substitutes_a_newer_recommendation_for_the_entry_snapshot(self):
        """F. No current-value substitution. A second, later recommendation
        fixture exists (different technical/fundamental/sentiment/signal
        values, as if the model re-ran or the stock was re-researched after
        this trade was already opened) — the report for the ORIGINAL trade
        must still reflect the ORIGINAL entry snapshot, never the newer
        one, because build_report_payload/generate_and_persist never
        accept or look up a live/current recommendation at all: they are
        pure functions of exactly the (postmortem, entry_snapshot,
        exit_snapshot) objects passed to them by the caller. This proves
        the invariant two ways: (1) by injecting a later fixture with
        different values and confirming the report is unaffected by its
        mere existence, and (2) at the persistence/report boundary itself
        — build_report_payload's signature has no parameter through which
        a live/current recommendation could enter."""
        newer_ctx = RecommendationContext(
            evidence_source="DAILY_PICK",
            simulated_execution_price=140.0,
            recommendation_signal="SELL",
            recommendation_generated_at=dt.datetime(2026, 6, 6, 9, 0, tzinfo=UTC),
            recommendation_reference_price=140.0,
            confidence_score=20.0,
            technical_signal="SELL",
            technical_rsi=22.0,
            technical_macd_diff=-2.1,
            fundamental_score=30.0,
            sentiment_score=15.0,
            sentiment_label="NEGATIVE",
            daily_pick_run_id="run-2026-06-06-z",
            daily_pick_rank=1,
            model_version="alpha-engine-v8",
        )
        # A later/different recommendation fixture genuinely exists in the
        # test — e.g. as if trade 900002 were opened afterward against a
        # fresh run — but is never wired into trade 900001's report below.
        newer_snapshot_for_a_different_trade = build_entry_snapshot(
            paper_trade_id=900002, user_id="user-aaa", symbol="AAPL", market="US", ctx=newer_ctx,
        )
        assert newer_snapshot_for_a_different_trade.recommendation_signal == "SELL"

        original_snapshot = _build_original_snapshot(trade_id=900001)
        record = _closed_trade_record(trade_id=900001)
        exit_snapshot = _build_exit_snapshot_for(trade_id=900001)
        postmortem = compute_postmortem(record, snapshot=original_snapshot)

        payload = build_report_payload(postmortem, original_snapshot, exit_snapshot=exit_snapshot)

        assert _evidence_item(payload, "recommendation_signal")["value"] == "BUY"
        assert _evidence_item(payload, "technical_signal")["value"] == "BUY"
        assert _evidence_item(payload, "fundamental_score")["value"] == 71.0
        assert _evidence_item(payload, "sentiment_score")["value"] == 64.0
        # None of the newer values leak into trade 900001's report.
        for item in payload.evidence_items:
            if item["category"] == "entry_snapshot":
                assert item["value"] != "SELL"

        # Persistence-boundary proof: build_report_payload/generate_and_
        # persist accept only (postmortem, entry_snapshot, exit_snapshot)
        # — there is no live-recommendation/current-prediction parameter
        # they could have used instead, so a newer recommendation existing
        # elsewhere is structurally incapable of being substituted in.
        import inspect
        from services.postmortem.generation_service import build_report_payload as _brp
        from services.postmortem.generation_service import generate_and_persist as _gap

        brp_params = set(inspect.signature(_brp).parameters)
        gap_params = set(inspect.signature(_gap).parameters)
        assert brp_params == {"postmortem", "entry_snapshot", "exit_snapshot"}
        for forbidden in ("prediction", "live_recommendation", "current_recommendation", "daily_pick", "provider"):
            assert forbidden not in brp_params
            assert forbidden not in gap_params

    def test_full_lifecycle_via_generate_and_persist(self):
        """The conn-touching persistence wrapper also only ever reflects
        the passed-in entry_snapshot object — proven against a minimal
        fake conn matching the pattern in
        test_postmortem_generation_service.py (no real DB)."""
        from contextlib import contextmanager
        import json

        class _FakeConn:
            """Matches the fake in test_postmortem_generation_service.py's
            TestGenerateAndPersist: a minimal INSERT-returns-a-row,
            SELECT-finds-by-version-key connection. Kept local (not
            imported) because that class is that module's own private
            test fixture, not shared production/test infrastructure."""

            def __init__(self):
                self.report_rows = {}
                self.next_id = 1
                self.last_insert_params = None

            def execute(self, sql, params):
                stripped = sql.strip()
                if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
                    self.last_insert_params = params
                    (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
                     ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings, supersedes_id) = params
                    key = (paper_trade_id, schema_v, calc_v, rules_v)
                    for row in self.report_rows.values():
                        if (row[1], row[6], row[7], row[8]) == key:
                            self._pending = None
                            return self
                    new_id = self.next_id
                    self.next_id += 1
                    row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                           bundle_v, ev_hash, status, json.loads(structured), json.loads(ev_items),
                           json.loads(claims), json.loads(manifest), json.loads(gaps), json.loads(warnings),
                           dt.datetime(2026, 6, 5, tzinfo=UTC), supersedes_id)
                    self.report_rows[new_id] = row
                    self._pending = row
                    return self
                if stripped.startswith("SELECT") and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
                    paper_trade_id, schema_v, calc_v, rules_v = params
                    key = (paper_trade_id, schema_v, calc_v, rules_v)
                    for row in self.report_rows.values():
                        if (row[1], row[6], row[7], row[8]) == key:
                            self._pending = row
                            return self
                    self._pending = None
                    return self
                raise AssertionError(f"unexpected SQL: {sql!r}")

            def fetchone(self):
                return self._pending

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            @contextmanager
            def transaction(self):
                yield self

        conn = _FakeConn()
        snapshot = _build_original_snapshot()
        record = _closed_trade_record()
        exit_snapshot = _build_exit_snapshot_for()
        postmortem = compute_postmortem(record, snapshot=snapshot)

        from services.postmortem.generation_service import generate_and_persist

        report, created = generate_and_persist(
            conn, trade_id=900001, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=snapshot, exit_snapshot=exit_snapshot,
        )

        assert created is True
        assert report.calculation_version == CALCULATION_VERSION
        rec_item = next(i for i in report.evidence_items if i["name"] == "recommendation_signal")
        assert rec_item["value"] == "BUY"
        assert rec_item["verification_level"] == VerificationLevel.CLIENT_REPORTED.value

        # The exact params handed to the real SQL INSERT contain the
        # original entry-time value too, i.e. what actually gets persisted
        # (not merely what the in-memory dataclass says). Per
        # report_store.persist_report's own INSERT column list, index 12
        # is `evidence_items` (0=paper_trade_id ... 11=structured_report,
        # 12=evidence_items).
        evidence_items_json = conn.last_insert_params[12]
        persisted_items = json.loads(evidence_items_json)
        persisted_rec_item = next(i for i in persisted_items if i["name"] == "recommendation_signal")
        assert persisted_rec_item["value"] == "BUY"


@pytest.mark.unit
class TestPhaseA1EntryEvidenceLifecycleDailyPickSparse:
    """Fix 5 (owner-audit correction, Phase A1) — a sub-case fixture
    matching what the CURRENT Daily Pick path (buildEntryEvidenceFromDailyPick
    in frontend/src/utils/entryEvidence.ts) actually sends: technical_signal
    and sentiment_score are always null (Daily Pick's Pick type carries only
    tech_score/a sentiment label, not a governed technical_signal string or a
    numeric sentiment_score), and daily_pick_run_id/daily_pick_rank/
    model_version are also always null (no governed run/rank identifier
    exists in the live /api/picks payload). recommendation_signal is always
    "BUY" (Daily Picks only ever surface BUY-side picks) and fundamental_score
    IS populated (from pick.fund_score)."""

    def _daily_pick_sparse_ctx(self) -> RecommendationContext:
        return RecommendationContext(
            evidence_source="DAILY_PICK",
            simulated_execution_price=52.0,
            recommendation_signal="BUY",
            recommendation_generated_at=dt.datetime(2026, 6, 1, 6, 0, tzinfo=UTC),
            recommendation_reference_price=50.0,
            recommendation_entry_low=49.0,
            recommendation_entry_high=51.0,
            recommended_stop_loss=45.0,
            recommended_target_price=60.0,
            confidence_score=68.0,
            technical_signal=None,
            technical_rsi=None,
            technical_macd_diff=None,
            fundamental_score=58.0,
            sentiment_score=None,
            sentiment_label="NEUTRAL",
            market_regime_trend=None,
            market_regime_score_adj=None,
            market_regime_reason=None,
            recommendation_reasoning=["MACD bullish cross"],
            daily_pick_run_id=None,
            daily_pick_rank=None,
            model_version=None,
        )

    def test_daily_pick_sparse_snapshot_matches_real_frontend_behavior(self):
        snapshot = build_entry_snapshot(
            paper_trade_id=900003, user_id="user-bbb", symbol="MSFT", market="US",
            ctx=self._daily_pick_sparse_ctx(), level_history_contract_version="1.0.0",
        )
        assert snapshot.evidence_source == "DAILY_PICK"
        assert snapshot.recommendation_signal == "BUY"
        assert snapshot.fundamental_score == 58.0
        # The fields the live Daily Pick path never populates stay null —
        # asserting a snapshot built from a genuinely sparse DAILY_PICK
        # context does not silently backfill/fabricate any of them.
        assert snapshot.technical_signal is None
        assert snapshot.sentiment_score is None
        assert snapshot.daily_pick_run_id is None
        assert snapshot.daily_pick_rank is None
        assert snapshot.model_version is None

        record = ClosedTradeRecord(
            trade_id=900003, status="CLOSED", symbol="MSFT", market="US", quantity=5,
            entry_price=52.0, exit_price=58.0, stop_loss=45.0, target_price=60.0,
            opened_at=dt.datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
            closed_at=dt.datetime(2026, 6, 4, 15, 0, tzinfo=UTC),
            trade_management_mode="manual", exit_reason="MANUAL",
        )
        exit_snapshot = build_exit_snapshot(
            paper_trade_id=900003, user_id="user-bbb", symbol="MSFT", market="US",
            exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
            exit_price=58.0, exit_quantity=5,
            closed_at=dt.datetime(2026, 6, 4, 15, 0, tzinfo=UTC),
            realized_pnl_abs=30.0, management_mode="manual",
        )
        postmortem = compute_postmortem(record, snapshot=snapshot)
        payload = build_report_payload(postmortem, snapshot, exit_snapshot=exit_snapshot)

        fundamental_item = _evidence_item(payload, "fundamental_score")
        assert fundamental_item["value"] == 58.0
        # No evidence_item claims a technical_signal or sentiment_score value
        # for a trade whose entry snapshot never captured either.
        tech_items = [i for i in payload.evidence_items if i["name"] == "technical_signal" and i["value"] is not None]
        sentiment_items = [i for i in payload.evidence_items if i["name"] == "sentiment_score" and i["value"] is not None]
        assert tech_items == []
        assert sentiment_items == []


@pytest.mark.unit
class TestBuyRequestToEntrySnapshotCoverage:
    """Fix 5 (owner-audit correction, Phase A1) — proves the REAL production
    Buy-time snapshot builder, `_build_snapshot_for_buy` in
    `backend/api/routers/paper_trading.py`, correctly threads a full
    `BuyRequest.entry_evidence` payload into the `EntrySnapshot` it returns,
    without needing ANY change to that function's production logic — it is
    called here exactly as `paper_buy()` calls it."""

    def test_build_snapshot_for_buy_threads_entry_evidence_exactly(self):
        import sys
        from pathlib import Path

        backend_dir = Path(__file__).resolve().parents[2]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from api.routers.paper_trading import BuyRequest, EntryEvidenceRequest, _build_snapshot_for_buy

        entry_evidence = EntryEvidenceRequest(
            recommendation_signal="BUY",
            recommendation_generated_at=(dt.datetime.now(UTC) - dt.timedelta(minutes=5)).isoformat(),
            recommendation_reference_price=100.0,
            recommendation_entry_low=98.0,
            recommendation_entry_high=102.0,
            recommended_stop_loss=90.0,
            recommended_target_price=130.0,
            confidence_score=82.0,
            technical_signal="BUY",
            technical_rsi=58.0,
            technical_macd_diff=1.25,
            fundamental_score=71.0,
            sentiment_score=64.0,
            sentiment_label="POSITIVE",
            market_regime_trend="BULLISH",
            market_regime_score_adj=3.5,
            market_regime_reason="Broad market uptrend confirmed by regime model",
            recommendation_reasoning=[{"indicator": "RSI", "signal": "BUY", "reason": "oversold"}],
            daily_pick_run_id=None,
            daily_pick_rank=None,
            model_version=None,
        )
        req = BuyRequest(
            symbol="AAPL", market="US", quantity=10, price=101.0,
            signal="BUY", horizon="medium",
            stop_loss=90.0, target_price=130.0,
            trade_management_mode="manual",
            evidence_source="RESEARCH",
            entry_evidence=entry_evidence,
        )

        snapshot = _build_snapshot_for_buy(
            trade_id=900004, user_id="user-ccc", symbol="AAPL", market="US", req=req,
            level_history_contract_version="1.0.0",
        )

        assert snapshot.paper_trade_id == 900004
        assert snapshot.user_id == "user-ccc"
        assert snapshot.evidence_source == "RESEARCH"
        assert snapshot.simulated_execution_price == 101.0
        assert snapshot.user_selected_stop_loss == 90.0
        assert snapshot.user_selected_target_price == 130.0
        assert snapshot.recommendation_signal == "BUY"
        assert snapshot.recommendation_reference_price == 100.0
        assert snapshot.recommendation_entry_low == 98.0
        assert snapshot.recommendation_entry_high == 102.0
        assert snapshot.recommended_stop_loss == 90.0
        assert snapshot.recommended_target_price == 130.0
        assert snapshot.confidence_score == 82.0
        assert snapshot.technical_signal == "BUY"
        assert snapshot.technical_rsi == 58.0
        assert snapshot.technical_macd_diff == 1.25
        assert snapshot.fundamental_score == 71.0
        assert snapshot.sentiment_score == 64.0
        assert snapshot.sentiment_label == "POSITIVE"
        assert snapshot.market_regime_trend == "BULLISH"
        assert snapshot.market_regime_score_adj == 3.5
        assert snapshot.market_regime_reason == "Broad market uptrend confirmed by regime model"
        assert snapshot.recommendation_reasoning == [{"indicator": "RSI", "signal": "BUY", "reason": "oversold"}]
        assert snapshot.daily_pick_run_id is None
        assert snapshot.daily_pick_rank is None
        assert snapshot.model_version is None

    def test_build_snapshot_for_buy_with_no_entry_evidence_produces_honest_nulls(self):
        import sys
        from pathlib import Path

        backend_dir = Path(__file__).resolve().parents[2]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from api.routers.paper_trading import BuyRequest, _build_snapshot_for_buy

        req = BuyRequest(
            symbol="TSLA", market="US", quantity=3, price=250.0,
            signal="HOLD", horizon="short",
        )
        snapshot = _build_snapshot_for_buy(
            trade_id=900005, user_id="user-ddd", symbol="TSLA", market="US", req=req,
        )
        assert snapshot.evidence_source == "MANUAL"
        assert snapshot.recommendation_signal is None
        assert snapshot.technical_signal is None
        assert snapshot.fundamental_score is None
        assert snapshot.sentiment_score is None
