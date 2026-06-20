"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { api } from "@/utils/api";
import { AuthProvider } from "@/lib/AuthContext";
import { NpsPopup } from "@/components/NpsPopup";

export function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60_000,          // 1 min global floor — pages that need fresher data override explicitly
        gcTime: 30 * 60_000,
        retry: 1,
        retryDelay: 5_000,          // wait 5s before retry instead of instant (avoids hammering cold server)
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,  // reconnect already triggers enough re-renders
      },
    },
  }));

  // Ping on load + every 9 min so Render never goes cold (spins down after 15 min idle).
  // After ping succeeds, silently kick off a prediction for RELIANCE so the backend's
  // cache is warm and the first real user request gets a fast response.
  useEffect(() => {
    const ping = () =>
      api.get("/health", { timeout: 30_000 })
        .then(() => {
          // Fire-and-forget: warm up the most common Indian + US stock
          api.get("/api/predictions/RELIANCE?market=IN&horizon=medium", { timeout: 5_000 }).catch(() => {});
          api.get("/api/predictions/AAPL?market=US&horizon=medium",     { timeout: 5_000 }).catch(() => {});
        })
        .catch(() => {});
    ping();
    const id = setInterval(ping, 9 * 60_000);
    return () => clearInterval(id);
  }, []);

  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        {children}
        <NpsPopup />
      </AuthProvider>
    </QueryClientProvider>
  );
}
