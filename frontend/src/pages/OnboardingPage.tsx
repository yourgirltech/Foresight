import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { supabase } from "../lib/supabase";
import { AuthCard, ErrorText, buttonClass, fieldClass } from "../components/AuthCard";
import { FullPageSpinner } from "../components/Spinner";

export function OnboardingPage() {
  const { status, refreshProfile, signOut } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "loading") return <FullPageSpinner />;
  if (status === "signedOut") return <Navigate to="/login" replace />;
  if (status === "ready") return <Navigate to="/" replace />;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    // SECURITY DEFINER RPC: creates the org and makes the caller its clinic_admin.
    const { error } = await supabase.rpc("bootstrap_organization", { org_name: name });
    if (error) {
      setBusy(false);
      setError(error.message);
      return;
    }
    await refreshProfile();
    setBusy(false);
    navigate("/", { replace: true });
  }

  return (
    <AuthCard
      title="Create your clinic"
      subtitle="You'll become its administrator and can invite teammates next."
      footer={
        <button onClick={() => void signOut()} className="text-slate-400 hover:text-slate-600">
          Sign out
        </button>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block text-sm font-medium text-slate-700">
          Clinic name
          <input
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Riverside Family Medicine"
            className={fieldClass}
          />
        </label>
        <button type="submit" disabled={busy} className={buttonClass}>
          {busy ? "Creating…" : "Create clinic"}
        </button>
        <ErrorText>{error}</ErrorText>
      </form>
    </AuthCard>
  );
}
