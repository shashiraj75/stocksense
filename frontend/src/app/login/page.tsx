"use client";
import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { TrendingUp, Mail, Lock } from "lucide-react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const inputCls = "w-full bg-dark-bg border border-dark-border rounded-xl px-4 py-2.5 text-sm text-white placeholder:text-gray-500 outline-none focus:border-brand-500 transition-colors";

export default function LoginPage() {
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading]   = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [forgotMode, setForgotMode] = useState(false);
  const [message, setMessage]   = useState<{ type: "error" | "success"; text: string } | null>(null);
  const router = useRouter();

  const handleGoogle = async () => {
    setGoogleLoading(true);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setMessage({ type: "error", text: error.message });
      setGoogleLoading(false);
    }
  };

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) throw error;
      router.push("/accept-terms");
    } catch (err: any) {
      setMessage({ type: "error", text: err.message });
    } finally {
      setLoading(false);
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/callback`,
      });
      if (error) throw error;
      setMessage({ type: "success", text: "Password reset link sent — check your inbox." });
    } catch (err: any) {
      setMessage({ type: "error", text: err.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[80vh] flex items-center justify-center px-4 py-8">
      <div className="w-full max-w-md space-y-6">

        {/* Logo */}
        <div className="text-center space-y-1">
          <Link href="/" className="inline-flex items-center gap-2 text-brand-500">
            <TrendingUp size={28} />
            <span className="text-2xl font-bold text-white">StockSense</span>
          </Link>
          <p className="text-gray-400 text-sm">AI-powered stock intelligence · Invite only</p>
        </div>

        <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-5">
          <h2 className="text-lg font-bold text-center">
            {forgotMode ? "Reset your password" : "Sign in to your account"}
          </h2>

          {!forgotMode && (
            <>
              <button
                onClick={handleGoogle}
                disabled={googleLoading}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-xl border border-dark-border bg-dark-bg hover:bg-dark-border transition-colors text-sm font-medium disabled:opacity-50"
              >
                <svg width="16" height="16" viewBox="0 0 48 48">
                  <path fill="#FFC107" d="M43.6 20H24v8h11.3C33.6 33.1 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34.1 6.5 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20c11 0 19.7-8 19.7-20 0-1.3-.1-2.7-.1-4z"/>
                  <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.5 16 19 13 24 13c3 0 5.8 1.1 7.9 3l5.7-5.7C34.1 6.5 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
                  <path fill="#4CAF50" d="M24 44c5.2 0 9.9-1.9 13.5-5l-6.2-5.2C29.5 35.5 26.9 36 24 36c-5.2 0-9.6-2.9-11.3-7.1l-6.5 5C9.6 39.7 16.3 44 24 44z"/>
                  <path fill="#1976D2" d="M43.6 20H24v8h11.3c-.9 2.4-2.5 4.4-4.6 5.8l6.2 5.2C40.8 35.5 44 30.2 44 24c0-1.3-.1-2.7-.4-4z"/>
                </svg>
                {googleLoading ? "Redirecting…" : "Continue with Google"}
              </button>
              <div className="flex items-center gap-3 text-xs text-gray-500">
                <div className="flex-1 h-px bg-dark-border" />or<div className="flex-1 h-px bg-dark-border" />
              </div>
            </>
          )}

          <form onSubmit={forgotMode ? handleForgot : handleSignIn} className="space-y-3">
            <div className="relative">
              <Mail size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="email" placeholder="Email address" value={email}
                onChange={e => setEmail(e.target.value)} required
                className={inputCls + " pl-9"}
              />
            </div>

            {!forgotMode && (
              <div className="relative">
                <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-500" />
                <input
                  type="password" placeholder="Password" value={password}
                  onChange={e => setPassword(e.target.value)} required minLength={6}
                  className={inputCls + " pl-9"}
                />
              </div>
            )}

            {message && (
              <p className={`text-xs px-3 py-2 rounded-lg ${message.type === "error" ? "bg-red-500/10 text-red-400" : "bg-green-500/10 text-green-400"}`}>
                {message.text}
              </p>
            )}

            <button
              type="submit" disabled={loading}
              className="w-full py-2.5 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium transition-colors disabled:opacity-50"
            >
              {loading ? "Please wait…" : forgotMode ? "Send reset link" : "Sign In"}
            </button>
          </form>

          <p className="text-center text-xs text-gray-500">
            <button
              onClick={() => { setForgotMode(f => !f); setMessage(null); }}
              className="text-brand-500 hover:underline"
            >
              {forgotMode ? "← Back to sign in" : "Forgot password?"}
            </button>
          </p>
        </div>

        <p className="text-center text-xs text-gray-600">
          Access is by invitation only. Contact the administrator to request access.
        </p>
      </div>
    </div>
  );
}
