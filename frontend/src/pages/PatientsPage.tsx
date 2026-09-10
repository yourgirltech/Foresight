import { useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth/useAuth";
import { apiFetch } from "../lib/api";
import type { PatientRow, PatientsResponse } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleDateString() : "—";
}

function Flag({ text }: { text: string }) {
  const danger = /denied|high-risk|needs review|emergency/.test(text);
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        danger ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-600"
      }`}
    >
      {text}
    </span>
  );
}

function Card({ p }: { p: PatientRow }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold text-slate-900">
          {p.name}
          {p.dob && <span className="ml-2 text-xs font-normal text-slate-400">DOB {p.dob}</span>}
        </h2>
        <span className="text-xs text-slate-400">last activity {when(p.last_activity)}</span>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {p.flags.length === 0 ? (
          <span className="text-xs text-slate-400">no AI flags</span>
        ) : (
          p.flags.map((f) => <Flag key={f} text={f} />)
        )}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-slate-400">Appointments</dt>
          <dd className="font-medium text-slate-700">{p.appointments}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Claims</dt>
          <dd className="font-medium text-slate-700">
            {p.claims}
            {p.denied_claims > 0 && <span className="text-red-600"> · {p.denied_claims} denied</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Billed</dt>
          <dd className="font-medium text-slate-700">${p.billed_amount.toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-400">Open AI items</dt>
          <dd className="font-medium text-slate-700">
            {p.high_risk_claims + p.eligibility_issues + p.reminder_issues}
          </dd>
        </div>
      </dl>

      <div className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-500">
        {p.payers.length > 0 && <span>Payers: {p.payers.join(", ")}</span>}
        {p.member_ids.length > 0 && (
          <span className="ml-3">Member IDs: {p.member_ids.join(", ")}</span>
        )}
      </div>
    </section>
  );
}

export function PatientsPage() {
  const { organization } = useAuth();
  const [data, setData] = useState<PatientsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    apiFetch<PatientsResponse>("/api/patients").then(setData).catch((e) => setError(String(e)));
  }, []);

  const shown = useMemo(() => {
    const list = data?.patients ?? [];
    const term = q.trim().toLowerCase();
    return term ? list.filter((p) => p.name.toLowerCase().includes(term)) : list;
  }, [data, q]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Patients</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · a unified record per patient (soft-keyed by name + DOB) rolled up
          from appointments, claims, coverage, eligibility, and reminder calls — with the AI&rsquo;s
          per-patient flags.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {data === null && !error && <p className="text-slate-400">Loading…</p>}

      {data && (
        <>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={`Search ${data.total} patients…`}
            className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          />
          {shown.length === 0 ? (
            <p className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
              {data.total === 0 ? "No patients yet — run a seed script." : "No match."}
            </p>
          ) : (
            <div className="space-y-4">
              {shown.map((p) => (
                <Card key={p.name} p={p} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
