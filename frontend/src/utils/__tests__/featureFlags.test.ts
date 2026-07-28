import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { isTradePostmortemDailyEnabled } from "@/utils/featureFlags";

const ORIGINAL_FLAG = process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;

function restoreFlag() {
  if (ORIGINAL_FLAG === undefined) {
    delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
  } else {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = ORIGINAL_FLAG;
  }
}

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
});

afterEach(() => {
  restoreFlag();
});

describe("isTradePostmortemDailyEnabled — PR #32 pre-merge correction", () => {
  it("resolves to disabled when the env var is unset (default)", () => {
    delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
    expect(isTradePostmortemDailyEnabled()).toBe(false);
  });

  it.each([
    ["1", true],
    ["true", true],
    ["True", true],
    ["TRUE", true],
    ["yes", true],
    ["on", true],
    ["  true  ", true],
    ["0", false],
    ["false", false],
    ["no", false],
    ["off", false],
    ["", false],
    ["   ", false],
    ["maybe", false],
    ["2", false],
    ["enabled", false],
  ])("NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED=%o -> %o", (raw, expected) => {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = raw;
    expect(isTradePostmortemDailyEnabled()).toBe(expected);
  });

  it("only an explicit accepted true value enables it — never a truthy-but-unrecognized string", () => {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = "please enable this";
    expect(isTradePostmortemDailyEnabled()).toBe(false);
  });
});
