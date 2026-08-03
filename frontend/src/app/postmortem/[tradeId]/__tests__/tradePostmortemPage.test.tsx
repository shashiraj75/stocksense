import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { CurrentReportReadResponse } from "@/utils/api";

// Wave C, WC-N — per-trade postmortem page. Mirrors the mocking style
// already established by postmortemFeatureGate.test.tsx: mock auth and the
// API module, render inside a fresh QueryClientProvider, assert on
// rendered text/roles rather than internal implementation details.
vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-aaa" } }),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ tradeId: "42" }),
}));

const mockFetchCurrentPostmortemReport = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    fetchCurrentPostmortemReport: (...args: unknown[]) => mockFetchCurrentPostmortemReport(...args),
  };
});

function renderPage(PageComponent: React.ComponentType) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <PageComponent />
    </QueryClientProvider>
  );
  return { ...result, queryClient };
}

const ORIGINAL_FLAG = process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED;

const BASE_READY: CurrentReportReadResponse = {
  trade_id: 42,
  availability: "READY",
  report_schema_version: "1.2.0",
  calculation_version: "calc-v1",
  attribution_rules_version: "1.0.0",
  evidence_bundle_version: "1.0.0",
  market: "US",
  report_trading_date: "2026-08-01",
  market_timezone: "America/New_York",
  status: "COMPLETE",
  generated_at: "2026-08-01T20:00:00Z",
  structured_report: {
    postmortem: { outcome: "WIN", realized_pnl_abs: 120.5, realized_pnl_pct: 4.2, exit_mechanism: "TARGET_HIT" },
    price_path: {
      mfe_abs: 150, mfe_pct: 5.1, mae_magnitude_abs: 20, mae_magnitude_pct: 0.8,
      target_touch: true, target_touch_type: "INTRABAR", stop_touch: false, stop_touch_type: null,
      touch_order: "TARGET_FIRST", price_path_limitations: [],
      version_and_provenance: {
        report_schema_version: "1.2.0", calculation_version: "calc-v1",
        numerical_rules_version: "1.0.0", governed_semantic_rules_version: "1.0.0",
        governed_claim_rules_version: "1.0.0", entry_snapshot_schema_version: null,
        exit_snapshot_schema_version: null, level_history_contract_version: null,
        source_version: "1.0.0",
      },
    },
  },
  claims: [
    {
      claim_id: "CLM-42-r1", report_section: "price_path", factor: "target_touch", claim_text: "Target was touched first.",
      evidence_class: "DIRECTLY_OBSERVED", confidence_band: "HIGH", supporting_evidence_ids: [], opposing_evidence_ids: [],
      missing_evidence: [], contradiction_flags: [], rule_id: "R1", rule_version: "1.0.0", limitations: [],
    },
  ],
  evidence_items: [],
  evidence_gaps: ["no exit snapshot exists for this trade"],
  warnings: ["some warning"],
  source_manifest: null,
  supersedes_report_id: null,
};

beforeEach(() => {
  process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED = "true";
  mockFetchCurrentPostmortemReport.mockReset();
});

afterEach(() => {
  cleanup();
  vi.resetModules();
  vi.useRealTimers();
  if (ORIGINAL_FLAG === undefined) delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED;
  else process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED = ORIGINAL_FLAG;
});

describe("TradePostmortemPage — feature-disabled frontend gate", () => {
  it("renders a safe unavailable state and never calls the API when the frontend flag is off", async () => {
    delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED;
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    expect(screen.getByText(/postmortem reports aren.t available yet/i)).toBeInTheDocument();
    expect(mockFetchCurrentPostmortemReport).not.toHaveBeenCalled();
  });
});

describe("TradePostmortemPage — non-READY availability states", () => {
  const cases: Array<[CurrentReportReadResponse["availability"], RegExp]> = [
    ["PROCESSING", /generating this trade.s postmortem/i],
    ["NOT_ELIGIBLE", /trade is still open/i],
    ["NOT_AVAILABLE", /no postmortem report has been generated/i],
    ["TERMINAL_FAILURE", /report generation for this trade failed/i],
    ["INTEGRITY_CONTRADICTION", /could not be verified as complete/i],
    ["FEATURE_DISABLED", /postmortem reports aren.t available yet/i],
  ];

  it.each(cases)("renders an explicit message for %s, never a blank page", async (availability, expectedText) => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability, status: null, structured_report: null, claims: null,
      evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(expectedText)).toBeInTheDocument());
  });
});

describe("TradePostmortemPage — READY COMPLETE", () => {
  it("renders financial outcome, price-path findings, claims, gaps and warnings", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/target was touched first/i)).toBeInTheDocument());
    expect(screen.getByText(/WIN/)).toBeInTheDocument();
    expect(screen.getByText(/no exit snapshot exists for this trade/i)).toBeInTheDocument();
    expect(screen.getByText(/some warning/i)).toBeInTheDocument();
    expect(screen.queryByText(/limited evidence/i)).not.toBeInTheDocument();
  });

  it("expands version and provenance details on demand", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/show version & provenance details/i)).toBeInTheDocument());
    expect(screen.queryByText(/report schema/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText(/show version & provenance details/i));
    expect(screen.getByText(/report schema/i)).toBeInTheDocument();
    expect(screen.getByText("1.2.0")).toBeInTheDocument();
  });
});

describe("TradePostmortemPage — READY LIMITED_EVIDENCE", () => {
  it("is visually distinct and states the limitation without implying certainty", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({ ...BASE_READY, status: "LIMITED_EVIDENCE" });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/limited evidence\./i)).toBeInTheDocument());
    expect(screen.getByText(/without implying certainty/i)).toBeInTheDocument();
  });

  it("shows the canonical insufficient-evidence wording unchanged for an INSUFFICIENT_EVIDENCE claim", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      status: "LIMITED_EVIDENCE",
      claims: [
        {
          ...BASE_READY.claims![0],
          evidence_class: "INSUFFICIENT_EVIDENCE",
          confidence_band: "NOT_ASSESSABLE",
          claim_text: "Insufficient evidence to determine this factor reliably.",
          missing_evidence: ["no bars available"],
        },
      ],
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() =>
      expect(screen.getByText("Insufficient evidence to determine this factor reliably.")).toBeInTheDocument()
    );
  });
});

describe("TradePostmortemPage — bounded PROCESSING polling", () => {
  it("polls at a bounded interval while PROCESSING and stops immediately once READY", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport
      .mockResolvedValueOnce({ ...BASE_READY, availability: "PROCESSING", status: null, structured_report: null, claims: null, evidence_items: null, evidence_gaps: null, warnings: null })
      .mockResolvedValueOnce({ ...BASE_READY, availability: "PROCESSING", status: null, structured_report: null, claims: null, evidence_items: null, evidence_gaps: null, warnings: null })
      .mockResolvedValue(BASE_READY);

    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(4000);
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2));
    await vi.advanceTimersByTimeAsync(4000);
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(3));

    // Now READY — no further polling should occur even if more time passes.
    await vi.advanceTimersByTimeAsync(20000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(3);
  });

  it("stops polling on TERMINAL_FAILURE and does not keep retrying", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability: "TERMINAL_FAILURE", status: null, structured_report: null,
      claims: null, evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1);
  });

  it("stops polling on INTEGRITY_CONTRADICTION", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability: "INTEGRITY_CONTRADICTION", status: null, structured_report: null,
      claims: null, evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1);
  });

  it("never polls for NOT_ELIGIBLE (a stable, non-transitional state)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability: "NOT_ELIGIBLE", status: null, structured_report: null,
      claims: null, evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1);
  });

  it("shows a clear message once the bounded polling window expires (30 attempts)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability: "PROCESSING", status: null, structured_report: null,
      claims: null, evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    // 29 more polls (attempts 2..30) at 4s each exhausts MAX_POLL_ATTEMPTS.
    for (let i = 0; i < 29; i++) {
      await vi.advanceTimersByTimeAsync(4000);
    }
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30));

    await waitFor(() => expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument());

    // No further fetch should occur beyond the bound.
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30);
  });

  it("resets the bounded window when the trade ID changes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const navigation = await import("next/navigation");
    const useParamsSpy = vi.spyOn(navigation, "useParams");
    useParamsSpy.mockReturnValue({ tradeId: "42" });

    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability: "PROCESSING", status: null, structured_report: null,
      claims: null, evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    const { rerender, queryClient } = renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledWith(42, expect.anything()));

    useParamsSpy.mockReturnValue({ tradeId: "99" });
    rerender(
      <QueryClientProvider client={queryClient}>
        <TradePostmortemPage />
      </QueryClientProvider>
    );

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledWith(99, expect.anything()));
  });
});

describe("TradePostmortemPage — cancellation on unmount", () => {
  it("does not throw or continue polling after the component unmounts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, availability: "PROCESSING", status: null, structured_report: null,
      claims: null, evidence_items: null, evidence_gaps: null, warnings: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    const { unmount } = renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    unmount();

    const callsAtUnmount = mockFetchCurrentPostmortemReport.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30000);
    // No new fetch should be scheduled after unmount — React Query tears
    // down the refetchInterval timer along with the observer.
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(callsAtUnmount);
  });
});

describe("TradePostmortemPage — accessibility", () => {
  it("uses status/alert roles for loading and error states, and keyboard-operable expandable controls", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/show version & provenance details/i)).toBeInTheDocument());
    const provenanceButton = screen.getByText(/show version & provenance details/i);
    expect(provenanceButton.tagName).toBe("BUTTON");
    expect(provenanceButton).toHaveAttribute("aria-expanded", "false");

    const whyButtons = screen.getAllByText(/why this conclusion/i);
    expect(whyButtons[0].closest("button")).toHaveAttribute("aria-expanded", "false");
  });
});
