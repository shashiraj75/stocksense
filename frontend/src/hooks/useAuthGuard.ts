"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/AuthContext";

export function useAuthGuard() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    // Check terms acceptance via cookie
    const termsAccepted = document.cookie.includes("ss_terms=v1.0");
    const metaAccepted = user.user_metadata?.terms_accepted === true;
    if (!termsAccepted && !metaAccepted) {
      router.replace("/accept-terms");
    }
  }, [user, loading, router]);

  return { user, loading };
}
