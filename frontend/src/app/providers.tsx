"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { api } from "@/utils/api";
import { AuthProvider } from "@/lib/AuthContext";

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

  // Ping backend on app load so Render wakes up before the user needs data
  useEffect(() => {
    api.get("/health", { timeout: 60_000 }).catch(() => {});
  }, []);

  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
