import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { RiskBadge, StatusBadge } from "../components/claims";
import { apiFetch } from "../lib/api";
import type { ClaimSummary } from "../lib/types";

function money(v: string | number): string {
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? `$${n.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : String(v);
}

export function ClaimsPage() {
  const { organization } = useAuth();
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<{ claims: ClaimSummary[] }>("/api/claims")
      .then((r) => setClaims(r.claims))
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Claims</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · AI reviews each claim; a human approves anything consequential.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Claim</th>
              <th className="px-4 py-3">Patient</th>
              <th className="px-4 py-3">Payer</th>
              <th className="px-4 py-3 text-right">Amount</th>
              <th className="px-4 py-3">Risk</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {claims === null && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  Loading…
                </td>
              </tr>
            )}
            {claims?.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No claims yet. Run <code>scripts/seed_claims.py</code> to load synthetic claims.
                </td>
              </tr>
            )}
            {claims?.map((c) => (
              <tr key={c.id} className="hover:bg-slate-50">
                <td className="px-4 py-3 font-medium text-brand-700">
                  <Link to={`/claims/${c.id}`} className="hover:underline">
                    {c.claim_id}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-600">{c.patient_name}</td>
                <td className="px-4 py-3 text-slate-600">{c.payer_name ?? "—"}</td>
                <td className="px-4 py-3 text-right tabular-nums text-slate-700">{money(c.amount)}</td>
                <td className="px-4 py-3">
                  <RiskBadge level={c.risk_level} score={c.risk_score} />
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={c.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
