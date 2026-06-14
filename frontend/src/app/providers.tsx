"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { api } from "@/utils/api";

export function Providers({ children }: { children: React.ReactNode }) {
  const [qc] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5 * 60_000,
        gcTime: 30 * 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  }));

  // Ping backend on app load so Render wakes up before the user needs data
  useEffect(() => {
    api.get("/health", { timeout: 60_000 }).catch(() => {});
  }, []);

  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}
