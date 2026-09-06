import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { EligibilityBadge, EmergencyPill } from "../components/eligibility";
import { apiFetch } from "../lib/api";
import type { EligibilityListResponse } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

export function AppointmentsPage() {
  const { organization } = useAuth();
  const [data, setData] = useState<EligibilityListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<EligibilityListResponse>("/api/eligibility")
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Appointments &amp; eligibility</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · verification runs ahead of a scheduled visit to inform staff. For an
          emergency patient it runs in parallel with care — it never gates or delays anything.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {/* --- Scheduled appointments --- */}
      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-700">Scheduled appointments</h2>
        </div>
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Scheduled</th>
              <th className="px-4 py-3">Patient</th>
              <th className="px-4 py-3">Member ID</th>
              <th className="px-4 py-3">Payer</th>
              <th className="px-4 py-3">Eligibility</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data === null && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  Loading…
                </td>
              </tr>
            )}
            {data?.appointments.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  No appointments yet. Run <code>scripts/seed_eligibility.py</code>.
                </td>
              </tr>
            )}
            {data?.appointments.map((a) => (
              <tr key={a.id} className="hover:bg-slate-50">
                <td className="px-4 py-3 text-slate-600">
                  <Link to={`/app/appointments/${a.id}`} className="font-medium text-brand-700 hover:underline">
                    {when(a.scheduled_at)}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-700">{a.patient_name}</td>
                <td className="px-4 py-3 tabular-nums text-slate-500">
                  {a.patient_member_id || <span className="text-slate-400">—</span>}
                </td>
                <td className="px-4 py-3 text-slate-600">{a.latest_check?.payer_name ?? "—"}</td>
                <td className="px-4 py-3">
                  <EligibilityBadge status={a.latest_check?.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* --- Emergency / unscheduled registrations --- */}
      <section className="overflow-hidden rounded-xl border border-rose-200 bg-white">
        <div className="flex items-center gap-3 border-b border-rose-100 bg-rose-50/40 px-4 py-3">
          <h2 className="text-sm font-semibold text-rose-900">Emergency / unscheduled registrations</h2>
          <EmergencyPill />
        </div>
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Registered</th>
              <th className="px-4 py-3">Patient</th>
              <th className="px-4 py-3">Member ID</th>
              <th className="px-4 py-3">Payer</th>
              <th className="px-4 py-3">Eligibility</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.unscheduled_checks.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
                  None.
                </td>
              </tr>
            )}
            {data?.unscheduled_checks.map((c) => (
              <tr key={c.id} className="hover:bg-slate-50">
                <td className="px-4 py-3 text-slate-600">
                  <Link to={`/app/eligibility/${c.id}`} className="font-medium text-brand-700 hover:underline">
                    {when(c.created_at)}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-700">{c.patient_name}</td>
                <td className="px-4 py-3 tabular-nums text-slate-500">
                  {c.patient_member_id || <span className="text-slate-400">unidentified</span>}
                </td>
                <td className="px-4 py-3 text-slate-600">{c.payer_name ?? "—"}</td>
                <td className="px-4 py-3">
                  <EligibilityBadge status={c.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="text-xs text-slate-400">
        A <span className="font-medium text-slate-500">check failed</span> or{" "}
        <span className="font-medium text-slate-500">needs more info</span> result on an emergency
        registration is recorded for a later re-check — it is never escalated and never blocks care.
      </p>
    </div>
  );
}
