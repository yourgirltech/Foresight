import { useEffect, useState } from "react";

import { useAuth } from "../auth/useAuth";
import { apiFetch } from "../lib/api";
import type { PayerRow, PayersResponse } from "../lib/types";

function YesNo({ v }: { v: boolean | null }) {
  if (v === null || v === undefined) return <span className="text-slate-400">—</span>;
  return v ? (
    <span className="text-emerald-700">Yes</span>
  ) : (
    <span className="text-slate-500">No</span>
  );
}

function PayerCard({ p }: { p: PayerRow }) {
  const r = p.rules;
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-slate-900">{p.name}</h2>
        <span className="text-xs text-slate-400">
          {p.claims.total} claims · {p.prior_auth.total} prior auths · {p.eligibility.total} eligibility checks
        </span>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Claims rule engine (06)
          </h3>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
            <dt className="text-slate-500">Authorization required</dt>
            <dd><YesNo v={r.authorization_required} /></dd>
            <dt className="text-slate-500">Documentation required</dt>
            <dd><YesNo v={r.documentation_required} /></dd>
            <dt className="text-slate-500">Follow-up threshold</dt>
            <dd>{r.follow_up_threshold_days ? `${r.follow_up_threshold_days} days` : "—"}</dd>
          </dl>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Eligibility (01)
          </h3>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
            <dt className="text-slate-500">On the real-time network</dt>
            <dd><YesNo v={r.eligibility_verification_supported} /></dd>
            <dt className="text-slate-500">Active-coverage threshold</dt>
            <dd>{r.eligibility_active_threshold ?? "—"}</dd>
          </dl>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Prior authorization (02)
          </h3>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
            <dt className="text-slate-500">Electronic PA channel</dt>
            <dd><YesNo v={r.prior_auth_supported} /></dd>
            <dt className="text-slate-500">Auth for elective set</dt>
            <dd><YesNo v={r.prior_auth_required_default} /></dd>
            <dt className="text-slate-500">Approval threshold</dt>
            <dd>{r.prior_auth_approval_threshold ?? "—"}</dd>
          </dl>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Outcomes so far
          </h3>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
            <dt className="text-slate-500">Claims billed</dt>
            <dd>${p.claims.billed_amount.toLocaleString()}</dd>
            <dt className="text-slate-500">Claims denied</dt>
            <dd className={p.claims.denied ? "text-red-600" : ""}>{p.claims.denied}</dd>
            <dt className="text-slate-500">PA denied</dt>
            <dd className={p.prior_auth.denied ? "text-red-600" : ""}>{p.prior_auth.denied}</dd>
            <dt className="text-slate-500">Eligibility inactive / failed</dt>
            <dd>{p.eligibility.inactive} / {p.eligibility.failed}</dd>
          </dl>
        </div>
      </div>
    </section>
  );
}

export function InsurancePage() {
  const { organization } = useAuth();
  const [data, setData] = useState<PayersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<PayersResponse>("/api/payers").then(setData).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Insurance</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · every payer on file and the rule config each AI agent checks a
          claim, an eligibility request, or a prior-authorization against.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {data === null && !error && <p className="text-slate-400">Loading…</p>}

      {data && data.payers.length === 0 && (
        <p className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
          No payers yet. Run a seed script (e.g. <code>scripts/seed_claims.py</code>).
        </p>
      )}

      {data?.payers.map((p) => <PayerCard key={p.id} p={p} />)}
    </div>
  );
}
