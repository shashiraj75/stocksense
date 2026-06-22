"use client";
import { Suspense, useState, useEffect } from "react";
import { supabase } from "@/lib/supabase";
import { TrendingUp, Mail, Lock } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

const inputCls = "w-full bg-dark-bg border border-dark-border rounded-xl px-4 py-2.5 text-sm text-white placeholder:text-gray-500 outline-none focus:border-brand-500 transition-colors";

function LoginForm() {
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading]   = useState(false);
  const [forgotMode, setForgotMode] = useState(false);
  const [message, setMessage]   = useState<{ type: "error" | "success"; text: string } | null>(null);
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (searchParams?.get("notice") === "invite_expired") {
      setMessage({
        type: "error",
        text: "That invite or reset link was already used (or has expired) — links only work once. Ask the administrator to send a fresh one.",
      });
    }
  }, [searchParams]);

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
            <span className="text-2xl font-bold text-white">StockSense360</span>
          </Link>
          <p className="text-gray-400 text-sm">AI-powered stock intelligence · Invite only</p>
        </div>

        <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-5">
          <h2 className="text-lg font-bold text-center">
            {forgotMode ? "Reset your password" : "Sign in to your account"}
          </h2>


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
          Access is by invitation only. If you were just invited, check your email for an
          invite link — it will ask you to set a password before you sign in here.
          No invite yet? Contact the administrator to request access.
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <LoginForm />
    </Suspense>
  );
}
