import { useState, type FormEvent } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { supabase } from "../lib/supabase";
import { AuthCard, ErrorText, buttonClass, fieldClass } from "../components/AuthCard";
import { FullPageSpinner } from "../components/Spinner";

export function SignupPage() {
  const { status } = useAuth();
  const [params] = useSearchParams();
  const inviteToken = params.get("token");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "loading") return <FullPageSpinner />;

  // Already signed in: send them where they belong.
  if (status === "ready") return <Navigate to="/" replace />;
  if (status === "noOrg") {
    return (
      <Navigate to={inviteToken ? `/accept-invite?token=${inviteToken}` : "/onboarding"} replace />
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const { error } = await supabase.auth.signUp({ email, password });
    setBusy(false);
    if (error) {
      setError(error.message);
      return;
    }
    // Local dev has email confirmation disabled, so a session is now active and
    // the AuthProvider will transition us to `noOrg` -> the redirect above.
  }

  return (
    <AuthCard
      title="Create your account"
      subtitle={
        inviteToken
          ? "Sign up to join the clinic you were invited to."
          : "Sign up, then create your clinic."
      }
      footer={
        <>
          Already have an account?{" "}
          <Link
            to={inviteToken ? `/login?token=${inviteToken}` : "/login"}
            className="font-medium text-brand-600 hover:text-brand-700"
          >
            Log in
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
            minLength={6}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={fieldClass}
          />
        </label>
        <button type="submit" disabled={busy} className={buttonClass}>
          {busy ? "Creating account…" : "Sign up"}
        </button>
        <ErrorText>{error}</ErrorText>
      </form>
    </AuthCard>
  );
}
