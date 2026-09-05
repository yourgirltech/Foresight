import { useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { supabase } from "../lib/supabase";
import { AuthCard, ErrorText, buttonClass } from "../components/AuthCard";
import { FullPageSpinner } from "../components/Spinner";
import type { Role } from "../lib/types";

interface Preview {
  organization_name: string;
  role: Role;
  email: string;
  status: "pending" | "accepted" | "revoked";
}

export function AcceptInvitePage() {
  const { status, refreshProfile, signOut, session } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token");

  const [preview, setPreview] = useState<Preview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token || status !== "noOrg") return;
    let active = true;
    (async () => {
      const { data, error } = await supabase.rpc("preview_invitation", {
        invitation_token: token,
      });
      if (!active) return;
      if (error) setError(error.message);
      else setPreview((data?.[0] as Preview) ?? null);
      setLoadingPreview(false);
    })();
    return () => {
      active = false;
    };
  }, [token, status]);

  if (status === "loading") return <FullPageSpinner />;
  if (status === "signedOut") {
    return <Navigate to={`/signup${token ? `?token=${token}` : ""}`} replace />;
  }
  if (status === "ready") return <Navigate to="/" replace />;
  if (!token) return <Navigate to="/onboarding" replace />;

  async function handleAccept() {
    setError(null);
    setBusy(true);
    const { error } = await supabase.rpc("accept_invitation", { invitation_token: token });
    if (error) {
      setBusy(false);
      setError(error.message);
      return;
    }
    await refreshProfile();
    setBusy(false);
    navigate("/", { replace: true });
  }

  const emailMismatch =
    preview && session?.user.email && preview.email.toLowerCase() !== session.user.email.toLowerCase();

  return (
    <AuthCard
      title="Join a clinic"
      subtitle={preview ? undefined : "Checking your invitation…"}
      footer={
        <button onClick={() => void signOut()} className="text-slate-400 hover:text-slate-600">
          Sign out
        </button>
      }
    >
      {loadingPreview ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : preview && preview.status === "pending" ? (
        <div className="space-y-4">
          <p className="text-sm text-slate-700">
            You've been invited to join{" "}
            <span className="font-semibold">{preview.organization_name}</span> as{" "}
            <span className="font-semibold">{preview.role}</span>.
          </p>
          {emailMismatch && (
            <ErrorText>
              This invitation was sent to {preview.email}, but you're signed in as{" "}
              {session?.user.email}. Sign out and sign back in with the invited address.
            </ErrorText>
          )}
          <button
            onClick={handleAccept}
            disabled={busy || Boolean(emailMismatch)}
            className={buttonClass}
          >
            {busy ? "Joining…" : `Join ${preview.organization_name}`}
          </button>
          <ErrorText>{error}</ErrorText>
        </div>
      ) : (
        <ErrorText>
          {error ?? "This invitation is no longer valid. Ask your clinic admin for a new one."}
        </ErrorText>
      )}
    </AuthCard>
  );
}
