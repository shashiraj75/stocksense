/**
 * Paper Trading proximity notification channel (2026-09-14, revised).
 *
 * An always-on in-page banner for "near target"/"near stop loss" was
 * added earlier the same day, then reverted per direct user feedback —
 * it crowded the page with continuous proximity messages for every open
 * position. The user wants on-screen feedback only for an actual
 * close/trigger event (already covered by closeMutation.onSuccess /
 * showManualBanner, untouched by this change), and confirmed proximity
 * email should stay off by default (unchanged from the earlier fix).
 *
 * These are structural/source tests — this page has no existing test
 * infrastructure to mount OpenTradeRow in isolation, mirroring this
 * codebase's own established convention for verifying wiring properties
 * directly against source text.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SOURCE = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");

describe("Paper Trading proximity notification channel", () => {
  it("no in-page banner is pushed for the routine near-target/near-stop-loss case", () => {
    expect(SOURCE).not.toContain("`${trade.id}-target-banner`");
    expect(SOURCE).not.toContain("`${trade.id}-stop-banner`");
  });

  it("the native OS popup remains the only proximity channel, still opt-in (gated by both notificationsEnabled and browser permission)", () => {
    const nativeEffectStart = SOURCE.indexOf("Native OS browser popup");
    expect(nativeEffectStart).toBeGreaterThan(-1);
    const nativeEffectBody = SOURCE.slice(nativeEffectStart, nativeEffectStart + 1500);
    expect(nativeEffectBody).toContain("if (!notificationsEnabled) return;");
    expect(nativeEffectBody).toContain('if (Notification.permission !== "granted") return;');
    expect(nativeEffectBody).toContain("`${trade.id}-target`");
    expect(nativeEffectBody).toContain("`${trade.id}-stop`");
  });

  it("an actual close/trigger event still pushes an in-page banner via closeMutation", () => {
    const closeMutationStart = SOURCE.indexOf("const closeMutation = useMutation({");
    expect(closeMutationStart).toBeGreaterThan(-1);
    const closeMutationBody = SOURCE.slice(closeMutationStart, closeMutationStart + 1200);
    expect(closeMutationBody).toContain("onNotify(");
  });
});
