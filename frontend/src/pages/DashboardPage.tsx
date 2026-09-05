import { useEffect, useState } from "react";

import { useAuth } from "../auth/useAuth";
import { apiFetch } from "../lib/api";

interface Me {
  user_id: string;
  email: string;
  organization_id: string;
  role: string;
  has_organization: boolean;
}

const placeholders = [
  { label: "Eligibility checks", hint: "Coming in a later phase" },
  { label: "Claims in progress", hint: "Coming in a later phase" },
  { label: "Denials to review", hint: "Coming in a later phase" },
];

export function DashboardPage() {
  const { organization, profile } = useAuth();
  const [me, setMe] = useState<Me | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<Me>("/api/me")
      .then(setMe)
      .catch((e) => setApiError(String(e)));
  }, []);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">{organization?.name}</h1>
        <p className="mt-1 text-sm text-slate-500">
          Signed in as {profile?.email} · {profile?.role}
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-3">
        {placeholders.map((c) => (
          <div key={c.label} className="rounded-xl border border-slate-200 bg-white p-5">
            <p className="text-sm font-medium text-slate-600">{c.label}</p>
            <p className="mt-2 text-3xl font-semibold text-slate-300">—</p>
            <p className="mt-1 text-xs text-slate-400">{c.hint}</p>
          </div>
        ))}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">Backend session check</h2>
        <p className="mt-1 text-xs text-slate-500">
          What <code>GET /api/me</code> resolves from your verified token — server-derived, not
          sent by this page.
        </p>
        {apiError ? (
          <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{apiError}</p>
        ) : (
          <pre className="mt-3 overflow-x-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700">
            {me ? JSON.stringify(me, null, 2) : "Loading…"}
          </pre>
        )}
      </section>

      <p className="text-sm text-slate-400">
        This shell is intentionally empty. Product features (eligibility, claims, voice) arrive in
        later phases — everything here is already scoped to {organization?.name} by database RLS.
      </p>
    </div>
  );
}
