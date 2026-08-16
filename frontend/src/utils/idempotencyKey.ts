// Trade Postmortem Evidence Completion, Phase A1 — Buy idempotency
// completion. Generates a client-side idempotency key matching the
// backend's existing format contract (backend/services/postmortem/
// idempotency.py: 8-128 chars, [a-zA-Z0-9_-] only) so it is never rejected
// by validate_idempotency_key_format.
//
// Invariant this module exists to uphold: ONE logical Buy decision = ONE
// stable key, reused across transport retry / lost-response retry / the
// same mutation retrying, but a NEW key for each new intentional Buy
// decision (changing quantity/horizon/stop/target/trade_management_mode
// before a new explicit Buy click is a new logical request and must get a
// new key). This module only generates keys; callers own storing one in a
// ref that persists across re-renders but resets on a genuine input change.

const KEY_PREFIX = "ptbuy";

/**
 * Generates a fresh, format-valid idempotency key for one logical Buy
 * decision. Uses crypto.randomUUID when available (all supported
 * browsers) with a fallback that stays within the backend's allowed
 * character set and length bounds.
 */
export function generateBuyIdempotencyKey(): string {
  const uuid =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : fallbackUuid();
  // crypto.randomUUID() is lowercase hex + hyphens, already within the
  // backend's allowed charset (letters, digits, "-", "_") and well within
  // the 8-128 length bound (36 chars + prefix).
  return `${KEY_PREFIX}-${uuid}`;
}

function fallbackUuid(): string {
  // Only reached in environments without crypto.randomUUID (very old
  // browsers) — still restricted to the backend's allowed charset.
  let out = "";
  for (let i = 0; i < 32; i++) {
    out += Math.floor(Math.random() * 16).toString(16);
  }
  return `${out}-${Date.now().toString(16)}`;
}
