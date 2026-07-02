"use client";
import { Suspense, useState, useEffect } from "react";
import { supabase } from "@/lib/supabase";
import { TrendingUp, Lock } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

const inputCls = "w-full bg-dark-bg border border-dark-border rounded-xl px-4 py-2.5 text-sm text-white placeholder:text-gray-500 outline-none focus:border-brand-500 transition-colors";

function SetPasswordForm() {
  const [password, setPassword]   = useState("");
  const [confirm, setConfirm]     = useState("");
  const [loading, setLoading]     = useState(false);
  const [checking, setChecking]   = useState(true);
  const [hasSession, setHasSession] = useState(false);
  const [message, setMessage]     = useState<{ type: "error" | "success"; text: string } | null>(null);
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams?.get("next") || "/accept-terms";

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      setHasSession(!!data.user);
      setChecking(false);
    });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);
    if (password.length < 6) {
      setMessage({ type: "error", text: "Password must be at least 6 characters." });
      return;
    }
    if (password !== confirm) {
      setMessage({ type: "error", text: "Passwords do not match." });
      return;
    }
    setLoading(true);
    try {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) throw error;
      router.push(next);
    } catch {
      // Never surface the raw Supabase message — may include session/token
      // internals the user can't act on and that don't belong in the UI.
      setMessage({ type: "error", text: "Unable to update the password. Please request a new reset link and try again." });
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!hasSession) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center px-4 py-8 text-center">
        <div className="w-full max-w-md space-y-4">
          <h1 className="text-xl font-bold text-white">Link expired or invalid</h1>
          <p className="text-sm text-gray-400">
            Your invitation or reset link has expired. Please ask the administrator to send a new invite, or request a fresh password reset link.
          </p>
          <Link href="/login" className="text-brand-500 hover:underline text-sm">← Back to sign in</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[80vh] flex items-center justify-center px-4 py-8">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-1">
          <Link href="/" className="inline-flex items-center gap-2 text-brand-500">
            <TrendingUp size={28} />
            <span className="text-2xl font-bold text-white">StockSense360</span>
          </Link>
          <p className="text-gray-400 text-sm">Set a password to finish setting up your account</p>
        </div>

        <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-5">
          <h2 className="text-lg font-bold text-center">Create your password</h2>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="relative">
              <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="password" placeholder="New password" value={password}
                onChange={e => setPassword(e.target.value)} required minLength={6}
                className={inputCls + " pl-9"}
              />
            </div>
            <div className="relative">
              <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="password" placeholder="Confirm password" value={confirm}
                onChange={e => setConfirm(e.target.value)} required minLength={6}
                className={inputCls + " pl-9"}
              />
            </div>

            {message && (
              <p className={`text-xs px-3 py-2 rounded-lg ${message.type === "error" ? "bg-red-500/10 text-red-400" : "bg-green-500/10 text-green-400"}`}>
                {message.text}
              </p>
            )}

            <button
              type="submit" disabled={loading}
              className="w-full py-2.5 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium transition-colors disabled:opacity-50"
            >
              {loading ? "Saving…" : "Save password & continue"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

export default function SetPasswordPage() {
  return (
    <Suspense fallback={
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <SetPasswordForm />
    </Suspense>
  );
}
