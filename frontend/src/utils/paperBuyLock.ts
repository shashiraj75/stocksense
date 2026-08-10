// Shared in-flight Buy lock — lives OUTSIDE PaperTradeModal's component
// state (module-level, not a useRef/useState), specifically so it survives
// the modal unmounting.
//
// Root cause this closes: PaperTradeModal.tsx's Cancel button and header X
// button call `onClose` unconditionally (not gated on buyMutation.isPending),
// which unmounts the modal. Unmounting destroys the component's local
// `useMutation` state (isPending resets to false) and its refs
// (frozenBuyRequestRef/buySubmissionInFlightRef), but the in-flight axios
// POST already sent to the server is NOT aborted. Reopening the modal for
// the same symbol/pick previously created a fresh, blank mutation instance
// that had no idea a Buy for that same logical operation was still
// resolving server-side — so Buy was clickable again. That is exactly how
// the TCS double-Buy incident happened.
//
// This module is the fix: a plain module-scoped Map, keyed by a stable
// "logical Buy operation id" (see `buyOperationKey` below), holding the
// frozen idempotency key and pending/terminal status for that operation.
// Any PaperTradeModal instance — the original one or a freshly remounted
// one for the same operation — reads/writes the SAME entry, so the lock
// survives unmount/remount. It deliberately does NOT lock other
// symbols/picks: the key includes symbol+market+horizon, so an unrelated
// Buy is never blocked by this.
//
// SAFETY BOUNDARY (owner-review item, explicit by design, not an
// oversight): this Map lives only in this browser tab's JS heap for the
// current page-load lifetime. It survives a component unmount/remount
// (that's its entire purpose), but it is NOT durable across a hard reload,
// a new tab, or a crashed/killed tab — all in-memory state is gone the
// instant the JS process restarts. It is a UX-layer defense-in-depth, not
// the source of truth: the backend's REQUIRED idempotency_key contract
// (backend/api/routers/paper_trading.py's BuyRequest.idempotency_key,
// enforced server-side via the durable `paper_trade_idempotency_key`
// table) is the actual authoritative safety net against a duplicate trade
// — it is what protects the user even if this Map is empty (e.g. right
// after a reload) for an operation that was still in flight server-side.
//
// LOCK LIFECYCLE (full, for the "pending" entries this module holds):
//   1. Created by `reserveBuyLock` the moment a Buy click actually fires
//      the mutation (PaperTradeModal.tsx), carrying the SAME idempotency
//      key already frozen into the request payload.
//   2. Survives this component instance unmounting (Cancel/X) — a
//      remounted instance for the same operation sees it via `getBuyLock`
//      and reuses its key / blocks a fresh submission.
//   3. Released (`releaseBuyLock`, entry removed entirely) on: (a) a
//      successful Buy response, or (b) a DEFINITIVE failure response (the
//      backend actually answered, even with an error) — both cases the
//      backend's own atomic transaction means its final state is known,
//      so the operation is safely terminal and the NEXT Buy for this pick
//      is free to mint a brand-new key.
//   4. Deliberately NOT released on an AMBIGUOUS failure (no response at
//      all — network drop/timeout/abort): the client cannot tell whether
//      the backend received/committed the request, so the key is retained
//      and the lock stays "pending" until either the SAME instance
//      retries with that same key (safe — the backend's reservation table
//      dedupes it) or the tab is closed/reloaded (at which point the
//      backend contract above is what still protects the user).
//   5. STALE-ENTRY SAFETY NET (fixes a real gap found in owner review): if
//      an entry is somehow never released — a truly abandoned ambiguous
//      failure the user never retries or reloads away from — it would
//      otherwise sit in this Map for the rest of the tab's lifetime
//      (unbounded growth across many symbols/sessions in a long-lived
//      tab). `MAX_LOCK_AGE_MS` bounds this: any entry older than that is
//      treated as abandoned and pruned the next time this module is
//      touched (`reserveBuyLock`/`getBuyLock`), not held onto forever.
//      This does NOT reintroduce the original race — 15 minutes is far
//      longer than any real Buy request or realistic user retry gap, so a
//      genuinely still-in-flight request is never pruned out from under
//      itself in practice, and the backend's own idempotency table is the
//      authoritative guard regardless.

export type PaperBuyLockEntry = {
  idempotencyKey?: string;
  status: "pending" | "done";
};

type InternalEntry = PaperBuyLockEntry & { reservedAt: number };

const locks = new Map<string, InternalEntry>();

// See "STALE-ENTRY SAFETY NET" above — 15 minutes.
const MAX_LOCK_AGE_MS = 15 * 60 * 1000;

function pruneStaleLocks(now: number = Date.now()): void {
  for (const [key, entry] of locks) {
    if (now - entry.reservedAt > MAX_LOCK_AGE_MS) {
      locks.delete(key);
    }
  }
}

/**
 * The stable identifier for "one logical Buy operation" that this lock is
 * keyed on. Deliberately narrower than just `symbol` (per the task's
 * requirement not to globally lock unrelated stocks) but stable across
 * modal remount for the SAME pick/intent: user + market + symbol + the
 * horizon the Buy was initiated for. Quantity/stop-loss/target-price are
 * intentionally excluded — those can be edited within the same still-open
 * logical Buy attempt (see PaperTradeModal's `materialInputsSignatureRef`)
 * without this being a "different" operation from the lock's point of view;
 * the lock only cares "is a Buy for this pick already in flight/just
 * completed", not the exact terms.
 */
export function buyOperationKey(userId: string, market: string, symbol: string, horizon: string): string {
  return `buy::${userId}::${market}::${symbol}::${horizon}`;
}

/**
 * Same idea as `buyOperationKey`, for the Close/Sell side (F) — keyed on
 * the specific open trade being closed, not on symbol, so it never blocks
 * closing an unrelated position. Sell requests don't carry a client
 * idempotency key today (the backend's row-level `SELECT ... FOR UPDATE` +
 * `status != 'OPEN'` check in close_service.py is the authoritative
 * dedup), so this lock only tracks pending/done for the UI's own
 * remount-survival purposes — it never mints or sends a key.
 */
export function closeOperationKey(userId: string, tradeId: number): string {
  return `close::${userId}::${tradeId}`;
}

/** Returns the existing lock entry for this operation, if any (pending or done). */
export function getBuyLock(key: string): PaperBuyLockEntry | undefined {
  pruneStaleLocks();
  return locks.get(key);
}

/**
 * Reserves the lock for a NEW logical Buy operation with a freshly minted
 * idempotency key, ONLY if no lock already exists for this key (i.e. there
 * is no in-flight or just-completed operation for this pick). Returns the
 * entry that is now current for this key — either the one just created, or
 * the pre-existing one if a race meant it was already reserved (callers
 * should treat that as "someone else already owns this operation" and NOT
 * fire a second request).
 */
export function reserveBuyLock(key: string, idempotencyKey?: string): PaperBuyLockEntry {
  pruneStaleLocks();
  const existing = locks.get(key);
  if (existing) return existing;
  const entry: InternalEntry = { idempotencyKey, status: "pending", reservedAt: Date.now() };
  locks.set(key, entry);
  return entry;
}

/** Marks the operation as terminal (success, or a definitive failure the
 * user will retry as a fresh intent) — clears the lock so the NEXT Buy for
 * this pick mints a genuinely new key. */
export function releaseBuyLock(key: string): void {
  locks.delete(key);
}

/** Test-only escape hatch — never called from application code. */
export function __clearAllBuyLocksForTests(): void {
  locks.clear();
}
