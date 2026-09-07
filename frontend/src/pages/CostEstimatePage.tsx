import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Printer } from "lucide-react";

import { apiFetch } from "../lib/api";
import type { CostEstimateDetail } from "../lib/types";

function money(n: number | string): string {
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function CostEstimatePage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<CostEstimateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<CostEstimateDetail>(`/api/cost-estimates/${id}`));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="space-y-4">
        <Link to="/app/appointments" className="text-sm text-brand-600 hover:underline">
          ← Appointments
        </Link>
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      </div>
    );
  }
  if (!data) return <p className="text-slate-400">Loading…</p>;

  const e = data.cost_estimate;
  const clinic = data.clinic_name ?? "Your clinic";
  const created = new Date(e.created_at).toLocaleDateString();

  return (
    <div className="space-y-4">
      {/* screen-only controls */}
      <div className="flex items-center justify-between print:hidden">
        {e.appointment_id ? (
          <Link
            to={`/app/appointments/${e.appointment_id}`}
            className="text-sm text-brand-600 hover:underline"
          >
            ← Appointment
          </Link>
        ) : (
          <span />
        )}
        <button
          onClick={() => window.print()}
          className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-600 hover:bg-slate-50"
        >
          <Printer className="h-4 w-4" />
          Print / Save PDF
        </button>
      </div>

      {/* the document */}
      <article className="mx-auto max-w-2xl rounded-xl border border-slate-200 bg-white p-8 text-slate-800 print:border-0 print:p-0 print:shadow-none">
        <header className="border-b border-slate-200 pb-4">
          <p className="text-sm font-semibold uppercase tracking-wide text-slate-500">{clinic}</p>
          <h1 className="mt-1 text-2xl font-bold text-slate-900">Good Faith Estimate</h1>
          <p className="mt-1 text-sm text-slate-500">
            Estimate for uninsured / self-pay services · prepared {created}
          </p>
        </header>

        <dl className="mt-4 grid grid-cols-2 gap-2 text-sm">
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">Patient</dt>
            <dd className="text-slate-800">{e.patient_name}</dd>
          </div>
          {e.patient_dob && (
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-400">Date of birth</dt>
              <dd className="text-slate-800">{e.patient_dob}</dd>
            </div>
          )}
        </dl>

        <table className="mt-6 w-full text-sm">
          <thead>
            <tr className="border-b border-slate-300 text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="py-2">Service</th>
              <th className="py-2">Code</th>
              <th className="py-2 text-right">Estimated charge</th>
            </tr>
          </thead>
          <tbody>
            {e.line_items.map((li, i) => (
              <tr key={i} className="border-b border-slate-100">
                <td className="py-2">{li.description || li.procedure_code}</td>
                <td className="py-2 tabular-nums text-slate-500">{li.procedure_code}</td>
                <td className="py-2 text-right tabular-nums">{money(li.base_price)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-semibold">
              <td className="py-2" colSpan={2}>
                Estimated total
              </td>
              <td className="py-2 text-right tabular-nums">{money(e.subtotal)}</td>
            </tr>
          </tfoot>
        </table>

        <section className="mt-6">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            In plain language
          </h2>
          <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-700">
            {e.patient_summary}
          </p>
        </section>

        <section className="mt-6 border-t border-slate-200 pt-4">
          <p className="whitespace-pre-line text-xs leading-relaxed text-slate-600">
            {e.disclaimer_text}
          </p>
          <p className="mt-2 text-[10px] text-slate-400">
            Disclaimer version {e.disclaimer_version}
          </p>
        </section>
      </article>
    </div>
  );
}
