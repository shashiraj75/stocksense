"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import { AuthProvider } from "@/lib/AuthContext";
import { NpsPopup } from "@/components/NpsPopup";
import { supabase } from "@/lib/supabase";

// Supabase's dashboard "Invite user" / password-reset emails use the implicit
// flow: they redirect to the bare Site URL with #access_token=...&type=invite
// (or type=recovery) in the hash fragment — never hitting /auth/callback,
// since hash fragments never reach the server. Unlike the classic supabase-js
// browser client, @supabase/ssr's createBrowserClient does NOT auto-parse
// hash tokens (detectSessionInUrl), so the session must be established
// explicitly via setSession() before navigating anywhere.
//
// Deliberately NOT gated on a specific `type=` value. Re-inviting an email
// that already has a row in auth.users (e.g. someone clicked an old invite
// before this fix shipped, which partially authenticates+confirms them) can
// make Supabase issue a different hash type than a fresh "invite" — observed
// as a user landing on the homepage with an unprocessed access_token and no
// session, then failing to sign in because they never got to set a password.
// Any hash carrying both tokens means "establish this session and let the
// user finish setup" regardless of which auth action produced it.
function InviteHashRedirect() {
  const router = useRouter();
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash) return;

    const params = new URLSearchParams(hash.slice(1));

    // Reused/expired/already-clicked links land here with #error=... instead
    // of tokens — Supabase tokens are single-use, so a link clicked twice (or
    // pre-fetched by an email client's link scanner) hits this path. Without
    // this branch the user landed on a silent homepage with no indication
    // anything was wrong, then failed to sign in because they'd never gotten
    // to set a password — surface it clearly instead.
    if (params.get("error")) {
      router.replace("/login?notice=invite_expired");
      return;
    }

    const access_token = params.get("access_token");
    const refresh_token = params.get("refresh_token");
    if (!access_token || !refresh_token) return;

    supabase.auth.setSession({ access_token, refresh_token }).then(() => {
      router.replace("/auth/set-password?next=/accept-terms");
    });
  }, [router]);
  return null;
}

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

  // Sprint 011 Phase 3 — bounded prediction warm-up, visibility-gated.
  // Keeps the backend's prediction cache warm for the two most common
  // stocks so the first real request each ~15-min cache window gets a fast
  // response, via the lightweight /signal route (Sprint 011 Phase 1): it
  // reads and warms the SAME symbol:market:horizon cache key and compute
  // path as the full route, but returns five fields instead of the full
  // multi-engine payload this effect was discarding anyway.
  //
  // The old client-side /health keep-alive is gone: its "Render spins down
  // after 15 min idle" rationale predates the Railway migration (Railway
  // doesn't sleep), and .github/workflows/keep_alive.yml already pings
  // /health every 10 minutes as the health monitor.
  //
  // Hidden tabs never ping: the interval only runs while the tab is
  // visible, is torn down when it hides, and a tab becoming visible again
  // warms once and restarts the interval (the `id` guard prevents interval
  // pile-up if visibilitychange ever fires visible twice in a row).
  useEffect(() => {
    const warm = () => {
      if (document.visibilityState !== "visible") return;
      // Fire-and-forget: warm up the most common Indian + US stock
      api.get("/api/predictions/RELIANCE/signal?market=IN&horizon=medium", { timeout: 5_000 }).catch(() => {});
      api.get("/api/predictions/AAPL/signal?market=US&horizon=medium",     { timeout: 5_000 }).catch(() => {});
    };
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (id !== null) return;
      warm();
      id = setInterval(warm, 9 * 60_000);
    };
    const stop = () => {
      if (id !== null) { clearInterval(id); id = null; }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };
    onVisibility();  // covers both the visible and hidden initial states
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <InviteHashRedirect />
        {children}
        <NpsPopup />
      </AuthProvider>
    </QueryClientProvider>
  );
}
