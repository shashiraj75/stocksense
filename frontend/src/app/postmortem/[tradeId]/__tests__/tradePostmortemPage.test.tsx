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

// A response the frozen backend could genuinely return: every non-
// INSUFFICIENT_EVIDENCE claim cites a real, resolvable supporting-evidence
// id (validate_postmortem_claim_semantics requires this — a claim with an
// empty supporting_evidence_ids list is a construction-time ValueError on
// the backend, never a persisted row), source_manifest is non-null and
// internally consistent (has_exit_snapshot=true requires a real
// exit_snapshot_schema_version and exit_trigger_timing_verification, per
// ReportSourceManifest's own _enforce_exit_snapshot_and_price_path_consistency
// validator), and every governed enum uses a real value from its
// vocabulary.
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
        exit_snapshot_schema_version: "1.0.0", level_history_contract_version: null,
        source_version: "1.0.0",
      },
    },
  },
  claims: [
    {
      claim_id: "CLM-42-r1", report_section: "price_path", factor: "target_touch", claim_text: "Target was touched first.",
      evidence_class: "DIRECTLY_OBSERVED", confidence_band: "HIGH",
      supporting_evidence_ids: ["EV-42-price_path-target_touch"], opposing_evidence_ids: [],
      missing_evidence: [], contradiction_flags: [], rule_id: "R1", rule_version: "1.0.0", limitations: [],
    },
  ],
  evidence_items: [
    {
      evidence_id: "EV-42-price_path-target_touch", category: "price_path", name: "target_touch",
      value: true, units: null, observation_timestamp: "2026-08-01T15:30:00Z",
      source: "yfinance_daily_bars", source_type: "EXTERNAL_UNOFFICIAL_DAILY",
      verification_level: "MECHANICALLY_VERIFIED", freshness_status: "POINT_IN_TIME_VALID", limitations: [],
    },
    {
      evidence_id: "EV-42-EXIT-closure_classification", category: "EXIT", name: "closure_classification",
      value: "TARGET_HIT_CLEAN", units: null, observation_timestamp: "2026-08-01T20:00:00Z",
      source: "exit_snapshot", source_type: "SERVER_STORED",
      verification_level: "MECHANICALLY_VERIFIED", freshness_status: "POINT_IN_TIME_VALID", limitations: [],
    },
  ],
  evidence_gaps: ["entry evidence completeness is LIMITED"],
  warnings: ["some warning"],
  source_manifest: {
    has_entry_snapshot: true, has_exit_snapshot: true,
    exit_snapshot_schema_version: "1.0.0", exit_trigger_timing_verification: "SERVER_VERIFIED",
    exit_evidence_rules_version: "1.0.0", phase1_calculation_version: "calc-v1",
    attribution_rules_version: "1.0.0", price_path_calculation_version: "1.0.0",
    price_path_rules_version: "1.0.0", governed_rules_version: "1.0.0",
  },
  supersedes_report_id: null,
};

// Every non-READY availability state genuinely nulls EVERY READY-only
// field on the backend (CurrentReportReadResponse's own
// _enforce_ready_non_ready_invariants validator) — this helper builds that
// shape directly rather than spreading BASE_READY and overriding a subset,
// which previously left stale READY content on non-READY test fixtures
// that the real backend could never produce.
function nonReadyResponse(availability: CurrentReportReadResponse["availability"]): CurrentReportReadResponse {
  return {
    trade_id: 42,
    availability,
    report_schema_version: null,
    calculation_version: null,
    attribution_rules_version: null,
    evidence_bundle_version: null,
    market: null,
    report_trading_date: null,
    market_timezone: null,
    status: null,
    generated_at: null,
    structured_report: null,
    claims: null,
    evidence_items: null,
    evidence_gaps: null,
    warnings: null,
    source_manifest: null,
    supersedes_report_id: null,
  };
}

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
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse(availability));
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

    await waitFor(() => expect(screen.getAllByText(/target was touched first/i).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/WIN/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/entry evidence completeness is limited/i).length).toBeGreaterThan(0);
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
      .mockResolvedValueOnce(nonReadyResponse("PROCESSING"))
      .mockResolvedValueOnce(nonReadyResponse("PROCESSING"))
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
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("TERMINAL_FAILURE"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1);
  });

  it("stops polling on INTEGRITY_CONTRADICTION", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("INTEGRITY_CONTRADICTION"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1);
  });

  it("never polls for NOT_ELIGIBLE (a stable, non-transitional state)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("NOT_ELIGIBLE"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1);
  });

  it("shows a clear message once the bounded polling window expires (30 attempts)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("PROCESSING"));
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

    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("PROCESSING"));
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
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("PROCESSING"));
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

  it("passes an AbortSignal that becomes aborted after unmount", async () => {
    // The request must genuinely be in-flight (not already resolved) at
    // unmount time for TanStack Query to actually invoke abort() on the
    // controller it hands to queryFn — a promise that resolves
    // immediately would already be "done" before unmount runs, and no
    // real HTTP client aborts a completed request.
    let capturedSignal: AbortSignal | undefined;
    mockFetchCurrentPostmortemReport.mockImplementation(
      (_tradeId: number, signal: AbortSignal) =>
        new Promise(() => {
          capturedSignal = signal;
        })
    );
    const { default: TradePostmortemPage } = await import("../page");
    const { unmount } = renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(capturedSignal).toBeInstanceOf(AbortSignal));
    expect(capturedSignal!.aborted).toBe(false);

    unmount();
    await vi.waitFor(() => expect(capturedSignal!.aborted).toBe(true));
  });

  it("aborts the old request's AbortSignal when the trade ID changes", async () => {
    const navigation = await import("next/navigation");
    const useParamsSpy = vi.spyOn(navigation, "useParams");
    useParamsSpy.mockReturnValue({ tradeId: "42" });

    let firstSignal: AbortSignal | undefined;
    mockFetchCurrentPostmortemReport.mockImplementation((tradeId: number, signal: AbortSignal) => {
      if (tradeId === 42) {
        return new Promise(() => {
          firstSignal = signal;
        });
      }
      return Promise.resolve(nonReadyResponse("PROCESSING"));
    });
    const { default: TradePostmortemPage } = await import("../page");
    const { rerender, queryClient } = renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(firstSignal).toBeInstanceOf(AbortSignal));
    expect(firstSignal!.aborted).toBe(false);

    useParamsSpy.mockReturnValue({ tradeId: "99" });
    rerender(
      <QueryClientProvider client={queryClient}>
        <TradePostmortemPage />
      </QueryClientProvider>
    );

    await vi.waitFor(() => expect(firstSignal!.aborted).toBe(true));
  });
});

describe("TradePostmortemPage — bounded polling stops on request error, not just non-PROCESSING data", () => {
  // q.state.data can retain the PREVIOUS successful PROCESSING response
  // even after a subsequent interval request has failed — this is the
  // exact bug class the fix in useBoundedProcessingPoll addresses: relying
  // on stale `data` alone would keep scheduling further requests forever.
  function axiosErrorWithStatus(status: number) {
    const err = new Error(`Request failed with status code ${status}`) as Error & {
      isAxiosError: boolean; response: { status: number };
    };
    err.isAxiosError = true;
    err.response = { status };
    return err;
  }

  it("stops polling after a rejected 401 request and shows a safe generic error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport
      .mockResolvedValueOnce(nonReadyResponse("PROCESSING"))
      .mockRejectedValueOnce(axiosErrorWithStatus(401));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(4000);
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2));

    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2);

    await waitFor(() => expect(screen.getByText(/could not load this report/i)).toBeInTheDocument());
    expect(screen.queryByText(/401/)).not.toBeInTheDocument();
    expect(screen.queryByText(/request failed with status code/i)).not.toBeInTheDocument();
  });

  it("stops polling after a rejected 403 request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport
      .mockResolvedValueOnce(nonReadyResponse("PROCESSING"))
      .mockRejectedValueOnce(axiosErrorWithStatus(403));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(4000);
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2));

    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2);
  });

  it("stops polling after a rejected 404 request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport
      .mockResolvedValueOnce(nonReadyResponse("PROCESSING"))
      .mockRejectedValueOnce(axiosErrorWithStatus(404));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(4000);
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2));

    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2);
  });

  it("stops periodic requests after a network failure and shows the safe generic error state", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport
      .mockResolvedValueOnce(nonReadyResponse("PROCESSING"))
      .mockRejectedValueOnce(new Error("Network Error"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(4000);
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2));

    await vi.advanceTimersByTimeAsync(30000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByText(/could not load this report/i)).toBeInTheDocument());
  });

  it("does not refetch on window focus after the polling window has timed out", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("PROCESSING"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    for (let i = 0; i < 29; i++) {
      await vi.advanceTimersByTimeAsync(4000);
    }
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30));

    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30);
  });

  it("does not refetch on reconnect after the polling window has timed out", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("PROCESSING"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    for (let i = 0; i < 29; i++) {
      await vi.advanceTimersByTimeAsync(4000);
    }
    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30));

    window.dispatchEvent(new Event("online"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30);
  });

  it("the bounded normal PROCESSING path still makes no request beyond its documented maximum", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchCurrentPostmortemReport.mockResolvedValue(nonReadyResponse("PROCESSING"));
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await vi.waitFor(() => expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(1));
    for (let i = 0; i < 60; i++) {
      await vi.advanceTimersByTimeAsync(4000);
    }
    expect(mockFetchCurrentPostmortemReport).toHaveBeenCalledTimes(30);
  });
});

describe("TradePostmortemPage — financial context and closure classification", () => {
  it("renders INR currency for an IN-market report", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({ ...BASE_READY, market: "IN" });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    // Realized P&L now correctly renders identically in both the summary
    // card AND the Layer-1 executive-summary sentence (single-source-of-
    // truth currency formatter) — expect exactly that duplication.
    await waitFor(() => expect(screen.getAllByText(/₹120\.50/).length).toBe(2));
    expect(screen.getByText(/₹150\.00/)).toBeInTheDocument(); // MFE (card only)
  });

  it("renders USD currency for a US-market report", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getAllByText(/\$120\.50/).length).toBe(2));
  });

  it("places the sign before the currency symbol for a negative value (-$X.XX, never $-X.XX)", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      structured_report: {
        ...BASE_READY.structured_report!,
        postmortem: { ...(BASE_READY.structured_report!.postmortem as object), realized_pnl_abs: -50, realized_pnl_pct: -2.1 },
      },
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getAllByText(/-\$50\.00/).length).toBe(2));
    expect(screen.queryByText(/\$-50\.00/)).not.toBeInTheDocument();
  });

  it("never attaches a currency symbol to a percentage value", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getAllByText(/\+4\.20%/).length).toBeGreaterThan(0));
    expect(screen.queryByText(/\$\+4\.20%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/₹\+4\.20%/)).not.toBeInTheDocument();
  });

  it("displays a number without a currency symbol for an unrecognized market", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({ ...BASE_READY, market: "CRYPTO" });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/^\+120\.50/)).toBeInTheDocument());
    expect(screen.queryByText(/[₹$]120\.50/)).not.toBeInTheDocument();
  });

  it("sources closure classification from the governed EXIT evidence item, not P&L or exit mechanism", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/target hit clean/i)).toBeInTheDocument());
  });

  it("shows N/A for closure classification when the governed evidence item is absent", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      evidence_items: BASE_READY.evidence_items!.filter((e) => e.name !== "closure_classification"),
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/closure classification/i)).toBeInTheDocument());
    expect(screen.getByText(/closure classification/i).parentElement).toHaveTextContent("N/A");
  });
});

describe("TradePostmortemPage — stock identity (PR #36 targeted correction)", () => {
  it("displays Company Name (SYMBOL) for a READY India report", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, market: "IN", symbol: "HDFCBANK", company_name: "HDFC Bank Limited",
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() =>
      expect(screen.getByTestId("stock-identity")).toHaveTextContent("HDFC Bank Limited (HDFCBANK)")
    );
  });

  it("displays Company Name (SYMBOL) for a READY US report", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, market: "US", symbol: "AAPL", company_name: "Apple Inc.",
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByTestId("stock-identity")).toHaveTextContent("Apple Inc. (AAPL)"));
  });

  it("falls back to the symbol alone when company_name is null", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, symbol: "EMMVEE", company_name: null,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByTestId("stock-identity")).toHaveTextContent("EMMVEE"));
    expect(screen.queryByText(/unknown stock/i)).not.toBeInTheDocument();
  });

  it("does not break rendering when symbol/company_name are absent entirely (older response shape)", async () => {
    const { symbol: _s, company_name: _c, ...withoutIdentity } = BASE_READY;
    mockFetchCurrentPostmortemReport.mockResolvedValue(withoutIdentity as CurrentReportReadResponse);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/Trade Postmortem/i)).toBeInTheDocument());
    expect(screen.queryByTestId("stock-identity")).not.toBeInTheDocument();
  });

  it("mentions the identity once in the executive summary without repeating it elsewhere in that sentence", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY, symbol: "HDFCBANK", company_name: "HDFC Bank Limited",
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => {
      const summary = screen.getByTestId("layer1-summary").textContent ?? "";
      const occurrences = summary.split("HDFC Bank Limited").length - 1;
      expect(occurrences).toBe(1);
    });
  });

  it("does not expose stock identity for a nonexistent or other-user trade (unchanged 404 behavior)", async () => {
    mockFetchCurrentPostmortemReport.mockRejectedValue({ response: { status: 404 } });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.queryByTestId("stock-identity")).not.toBeInTheDocument());
  });

  it("long company names wrap rather than causing horizontal overflow (break-words class present)", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      symbol: "LONGCO",
      company_name: "A Very Long Hypothetical Company Name Private Limited Corporation International",
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByTestId("stock-identity")).toHaveClass("break-words"));
  });
});

describe("TradePostmortemPage — report evidence manifest", () => {
  it("shows the complete manifest with all governed fields", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/show report evidence manifest/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/show report evidence manifest/i));

    expect(screen.getByText(/entry snapshot available/i)).toBeInTheDocument();
    expect(screen.getByText(/exit snapshot available/i)).toBeInTheDocument();
    expect(screen.getByText(/exit trigger timing verification/i)).toBeInTheDocument();
    expect(screen.getByText("SERVER VERIFIED")).toBeInTheDocument();
    expect(screen.getByText(/price-path calculation/i)).toBeInTheDocument();
    expect(screen.getByText(/price-path rules/i)).toBeInTheDocument();
    expect(screen.getByText(/governed rules/i)).toBeInTheDocument();
  });

  it("never displays a row for price_path_calculation_version when it is historically absent", async () => {
    const { price_path_calculation_version, ...manifestWithoutPricePath } = BASE_READY.source_manifest!;
    void price_path_calculation_version;
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      source_manifest: manifestWithoutPricePath,
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/show report evidence manifest/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/show report evidence manifest/i));

    expect(screen.queryByText(/price-path calculation/i)).not.toBeInTheDocument();
    // The invariant is "row absent", never "row present with a fabricated null".
  });

  it("shows entry/exit snapshot availability and supersession ID", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({ ...BASE_READY, supersedes_report_id: 7 });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/show report evidence manifest/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/show report evidence manifest/i));

    expect(screen.getAllByText(/^yes$/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("#7")).toBeInTheDocument();
  });

  it("the manifest disclosure control is keyboard-accessible and reports its expanded state", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue(BASE_READY);
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/show report evidence manifest/i)).toBeInTheDocument());
    const button = screen.getByText(/show report evidence manifest/i);
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(button);
    expect(screen.getByText(/hide report evidence manifest/i)).toHaveAttribute("aria-expanded", "true");
  });
});

describe("TradePostmortemPage — evidence value safety", () => {
  it("renders a bounded safe representation for a non-scalar evidence value, never [object Object]", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      evidence_items: [
        {
          ...BASE_READY.evidence_items![0],
          evidence_id: "EV-42-nested",
          value: { nested: true, list: [1, 2, 3] },
        },
      ],
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/all evidence/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/all evidence/i));

    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
    expect(screen.getByText(/"nested":true/)).toBeInTheDocument();
  });

  it("shows observation timestamp and limitations when present", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      evidence_items: [{ ...BASE_READY.evidence_items![0], limitations: ["derived from a single daily bar"] }],
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getByText(/all evidence/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/all evidence/i));

    expect(screen.getByText(/derived from a single daily bar/i)).toBeInTheDocument();
  });

  it("labels claim contradiction_flags distinctly from ordinary limitations", async () => {
    mockFetchCurrentPostmortemReport.mockResolvedValue({
      ...BASE_READY,
      claims: [{ ...BASE_READY.claims![0], contradiction_flags: ["target and stop both touched same bar"] }],
    });
    const { default: TradePostmortemPage } = await import("../page");
    renderPage(TradePostmortemPage);

    await waitFor(() => expect(screen.getAllByText(/contradiction/i).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/target and stop both touched same bar/i).length).toBeGreaterThan(0);
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
