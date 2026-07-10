import { describe, it, expect } from "vitest";
import { getSignalTone, resolveInitialTab } from "../page";

// Stock Detail AI Signal confidence coloring — extracted as getSignalTone
// purely so this doesn't require mounting the full page (router, react-query,
// auth context, live fetches). Test rendering-decision logic only; the
// confidence NUMBER itself is untouched, only the color it maps to.
describe("getSignalTone", () => {
  it("mutes a low-confidence BUY (< 45)", () => {
    const tone = getSignalTone("BUY", 20);
    expect(tone.text).toBe("text-gray-400");
    expect(tone.bar).toBe("bg-gray-500");
    expect(tone.container).toBe("bg-white/5 border-white/10");
  });

  it("shows a moderate-confidence BUY (45-59) as yellow", () => {
    const tone = getSignalTone("BUY", 50);
    expect(tone.text).toBe("text-yellow-400");
    expect(tone.bar).toBe("bg-yellow-500");
  });

  it("shows a strong-confidence BUY (>= 60) as green/bull", () => {
    const tone = getSignalTone("BUY", 85);
    expect(tone.text).toBe("text-bull");
    expect(tone.bar).toBe("bg-bull");
  });

  it("BUY confidence boundaries are inclusive at 45 and 60", () => {
    expect(getSignalTone("BUY", 45).text).toBe("text-yellow-400");
    expect(getSignalTone("BUY", 60).text).toBe("text-bull");
    expect(getSignalTone("BUY", 44).text).toBe("text-gray-400");
    expect(getSignalTone("BUY", 59).text).toBe("text-yellow-400");
  });

  it("SELL is always the same tone regardless of confidence — never muted", () => {
    expect(getSignalTone("SELL", 5)).toEqual({ container: "bg-bear/10 border-bear/30", text: "text-bear", bar: "bg-bear" });
    expect(getSignalTone("SELL", 95)).toEqual({ container: "bg-bear/10 border-bear/30", text: "text-bear", bar: "bg-bear" });
  });

  it("HOLD is always the same neutral tone regardless of confidence — never muted", () => {
    expect(getSignalTone("HOLD", 5)).toEqual({ container: "bg-white/5 border-white/10", text: "text-neutral", bar: "bg-neutral" });
    expect(getSignalTone("HOLD", 95)).toEqual({ container: "bg-white/5 border-white/10", text: "text-neutral", bar: "bg-neutral" });
  });
});

// Daily Picks horizon navigation — the Stock Detail side of the contract:
// an invalid/missing ?horizon= must still fall back safely to "short".
describe("resolveInitialTab", () => {
  it("returns 'medium' when ?horizon=medium", () => {
    expect(resolveInitialTab("medium")).toBe("medium");
  });

  it("returns 'long' when ?horizon=long", () => {
    expect(resolveInitialTab("long")).toBe("long");
  });

  it("falls back to 'short' when horizon is missing", () => {
    expect(resolveInitialTab(null)).toBe("short");
    expect(resolveInitialTab(undefined)).toBe("short");
  });

  it("falls back to 'short' for any invalid/unrecognized value", () => {
    expect(resolveInitialTab("short")).toBe("short");
    expect(resolveInitialTab("bogus")).toBe("short");
    expect(resolveInitialTab("")).toBe("short");
    expect(resolveInitialTab("MEDIUM")).toBe("short"); // case-sensitive, not normalized
  });
});
