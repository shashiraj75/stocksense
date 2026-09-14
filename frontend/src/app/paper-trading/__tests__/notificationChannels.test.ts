/**
 * Paper Trading notification channels — opt-in default correction
 * (2026-09-14). See the companion backend test
 * (test_paper_trading_notification_preference_opt_in.py) for the full
 * root-cause writeup.
 *
 * OpenTradeRow's proximity effects are too deeply embedded in a large,
 * data-fetching page component to mount in isolation without a substantial
 * new test harness (no test infrastructure exists for this page at all
 * yet) — these are structural/source tests, mirroring this codebase's own
 * established convention (e.g. the backend's test_daily_picks_raw_memory_
 * release.py) for verifying wiring properties directly against the source
 * text rather than skipping coverage entirely.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SOURCE = readFileSync(join(__dirname, "..", "page.tsx"), "utf-8");

describe("Paper Trading proximity notification channels", () => {
  it("has two distinct effects: an always-on in-page banner and an opt-in native popup", () => {
    expect(SOURCE).toContain("In-page banner — the reliable, always-on on-screen channel");
    expect(SOURCE).toContain("Native OS browser popup — an opt-in bonus layer");
  });

  it("the in-page banner effect is never gated by notificationsEnabled", () => {
    const bannerEffectStart = SOURCE.indexOf("In-page banner — the reliable");
    const nativeEffectStart = SOURCE.indexOf("Native OS browser popup");
    expect(bannerEffectStart).toBeGreaterThan(-1);
    expect(nativeEffectStart).toBeGreaterThan(bannerEffectStart);
    const bannerEffectBody = SOURCE.slice(bannerEffectStart, nativeEffectStart);
    expect(bannerEffectBody).not.toContain("if (!notificationsEnabled) return;");
    expect(bannerEffectBody).not.toContain('if (Notification.permission !== "granted") return;');
  });

  it("the in-page banner effect calls onNotify for both nearTarget and nearStopLoss with distinct dedup keys", () => {
    const bannerEffectStart = SOURCE.indexOf("In-page banner — the reliable");
    const nativeEffectStart = SOURCE.indexOf("Native OS browser popup");
    const bannerEffectBody = SOURCE.slice(bannerEffectStart, nativeEffectStart);
    expect(bannerEffectBody).toContain("`${trade.id}-target-banner`");
    expect(bannerEffectBody).toContain("`${trade.id}-stop-banner`");
    expect((bannerEffectBody.match(/onNotify\(/g) ?? []).length).toBe(2);
  });

  it("the native popup effect is still gated by both notificationsEnabled and browser permission (unchanged, opt-in)", () => {
    const nativeEffectStart = SOURCE.indexOf("Native OS browser popup");
    const nativeEffectBody = SOURCE.slice(nativeEffectStart, nativeEffectStart + 1500);
    expect(nativeEffectBody).toContain("if (!notificationsEnabled) return;");
    expect(nativeEffectBody).toContain('if (Notification.permission !== "granted") return;');
    expect(nativeEffectBody).toContain("`${trade.id}-target`");
    expect(nativeEffectBody).toContain("`${trade.id}-stop`");
  });

  it("both effects share the same module-level dedup set, but with distinct keys so one channel's dedup never suppresses the other", () => {
    expect(SOURCE).toContain("const _notifiedThisSession = new Set<string>();");
  });
});
