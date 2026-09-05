import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { supabase } from "../lib/supabase";
import { AuthCard, ErrorText, buttonClass, fieldClass } from "../components/AuthCard";
import { FullPageSpinner } from "../components/Spinner";

export function LoginPage() {
  const { status } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "loading") return <FullPageSpinner />;
  if (status === "ready") {
    const to = (location.state as { from?: Location } | null)?.from?.pathname ?? "/";
    return <Navigate to={to} replace />;
  }
  if (status === "noOrg") return <Navigate to="/onboarding" replace />;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) setError(error.message);
    // On success the AuthProvider picks up the session and the redirect above fires.
  }

  return (
    <AuthCard
      title="Log in"
      subtitle="Access your clinic's workspace."
      footer={
        <>
          No account?{" "}
          <Link to="/signup" className="font-medium text-brand-600 hover:text-brand-700">
            Sign up
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block text-sm font-medium text-slate-700">
          Email
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={fieldClass}
          />
        </label>
        <label className="block text-sm font-medium text-slate-700">
          Password
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={fieldClass}
          />
        </label>
        <button type="submit" disabled={busy} className={buttonClass}>
          {busy ? "Signing in…" : "Log in"}
        </button>
        <ErrorText>{error}</ErrorText>
      </form>
    </AuthCard>
  );
}
