import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { PriorAuthBadge, PriorAuthEmergencyPill } from "../components/priorAuth";
import { apiFetch } from "../lib/api";
import type { PriorAuthListResponse, PriorAuthorization, PriorAuthStatus } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleDateString() : "—";
}

const AWAITING: PriorAuthStatus[] = ["required_draft"];
const PAYER_ACTED: PriorAuthStatus[] = ["auth_denied", "info_needed"];
const RESOLVED: PriorAuthStatus[] = [
  "not_required",
  "emergency_exempt",
  "auth_approved",
  "submission_declined",
  "insufficient_info",
];

function Row({ pa }: { pa: PriorAuthorization }) {
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-3 text-slate-600">
        <Link to={`/app/prior-auth/${pa.id}`} className="font-medium text-brand-700 hover:underline">
          {pa.procedure_code || <span className="text-slate-400">no code</span>}
        </Link>
        {pa.procedure_description && (
          <span className="ml-2 text-xs text-slate-400">{pa.procedure_description}</span>
        )}
      </td>
      <td className="px-4 py-3 text-slate-700">{pa.patient_name}</td>
      <td className="px-4 py-3 text-slate-600">{pa.payer_name ?? "—"}</td>
      <td className="px-4 py-3 text-slate-500">{when(pa.created_at)}</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <PriorAuthBadge status={pa.status} />
          {pa.is_emergency && <PriorAuthEmergencyPill />}
        </div>
      </td>
    </tr>
  );
}

function Table({
  rows,
  empty,
}: {
  rows: PriorAuthorization[];
  empty: string;
}) {
  return (
    <table className="min-w-full divide-y divide-slate-200 text-sm">
      <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
        <tr>
          <th className="px-4 py-3">Procedure</th>
          <th className="px-4 py-3">Patient</th>
          <th className="px-4 py-3">Payer</th>
          <th className="px-4 py-3">Requested</th>
          <th className="px-4 py-3">Status</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {rows.length === 0 && (
          <tr>
            <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
              {empty}
            </td>
          </tr>
        )}
        {rows.map((pa) => (
          <Row key={pa.id} pa={pa} />
        ))}
      </tbody>
    </table>
  );
}

export function PriorAuthPage() {
  const { organization } = useAuth();
  const [data, setData] = useState<PriorAuthListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<PriorAuthListResponse>("/api/prior-authorizations")
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  const all = data?.prior_authorizations ?? [];
  const awaiting = all.filter((p) => AWAITING.includes(p.status));
  const payerActed = all.filter((p) => PAYER_ACTED.includes(p.status));
  const resolved = all.filter((p) => RESOLVED.includes(p.status));
  const inFlight = all.filter((p) => p.status === "submitting" || p.status === "submitted");

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Prior authorization</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · for an elective procedure, 02 checks whether the payer requires prior
          authorization ahead of time and drafts the request — a human approves submitting it.
          Emergency / urgent services are exempt and are never gated.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {data === null && !error && <p className="text-slate-400">Loading…</p>}

      {data && (
        <>
          <section className="overflow-hidden rounded-xl border border-amber-200 bg-white">
            <div className="border-b border-amber-100 bg-amber-50/50 px-4 py-3">
              <h2 className="text-sm font-semibold text-amber-900">
                Awaiting your approval
                {awaiting.length > 0 && (
                  <span className="ml-2 rounded-full bg-amber-200 px-1.5 py-0.5 text-xs text-amber-800">
                    {awaiting.length}
                  </span>
                )}
              </h2>
            </div>
            <Table rows={awaiting} empty="Nothing waiting on you." />
          </section>

          <section className="overflow-hidden rounded-xl border border-violet-200 bg-white">
            <div className="border-b border-violet-100 bg-violet-50/40 px-4 py-3">
              <h2 className="text-sm font-semibold text-violet-900">
                Payer responded — action needed
                {payerActed.length > 0 && (
                  <span className="ml-2 rounded-full bg-violet-200 px-1.5 py-0.5 text-xs text-violet-800">
                    {payerActed.length}
                  </span>
                )}
              </h2>
            </div>
            <Table rows={payerActed} empty="No denials or information requests." />
          </section>

          {inFlight.length > 0 && (
            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-4 py-3">
                <h2 className="text-sm font-semibold text-slate-700">Submitted — awaiting payer</h2>
              </div>
              <Table rows={inFlight} empty="—" />
            </section>
          )}

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-3">
              <h2 className="text-sm font-semibold text-slate-700">Resolved</h2>
            </div>
            <Table
              rows={resolved}
              empty="No prior authorizations yet. Run scripts/seed_prior_auth.py."
            />
          </section>
        </>
      )}
    </div>
  );
}
