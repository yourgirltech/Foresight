import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { apiFetch } from "../lib/api";
import type {
  CostEstimate,
  CostEstimateResult,
  PatientCoverage,
  ProcedurePrice,
} from "../lib/types";

function money(n: number | string): string {
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function isActive(c: PatientCoverage, today: string): boolean {
  if (!c.effective_date || c.effective_date > today) return false;
  return !c.termination_date || c.termination_date >= today;
}

/**
 * Shown on the appointment detail page ONLY when the patient has no active
 * coverage — a Good Faith Estimate under the No Surprises Act is for self-pay
 * individuals. The gate is also enforced server-side; this just hides the UI.
 */
export function CostEstimateSection({
  appointmentId,
  coverages,
}: {
  appointmentId: string;
  coverages: PatientCoverage[];
}) {
  const today = new Date().toISOString().slice(0, 10);
  const hasActiveCoverage = coverages.some((c) => isActive(c, today));

  const [existing, setExisting] = useState<CostEstimate | null>(null);
  const [prices, setPrices] = useState<ProcedurePrice[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [selfPay, setSelfPay] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CostEstimateResult | null>(null);

  useEffect(() => {
    if (hasActiveCoverage) return;
    apiFetch<{ cost_estimate: CostEstimate | null }>(
      `/api/appointments/${appointmentId}/cost-estimate`,
    )
      .then((d) => setExisting(d.cost_estimate))
      .catch(() => undefined);
    apiFetch<{ procedure_prices: ProcedurePrice[] }>("/api/procedure-prices")
      .then((d) => setPrices(d.procedure_prices))
      .catch((e) => setError(String(e)));
  }, [appointmentId, hasActiveCoverage]);

  const subtotalPreview = useMemo(() => {
    if (!prices) return 0;
    const byCode = new Map(prices.map((p) => [p.procedure_code, Number(p.base_price)]));
    return picked.reduce((sum, code) => sum + (byCode.get(code) ?? 0), 0);
  }, [picked, prices]);

  if (hasActiveCoverage) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">
          Cost estimate <span className="font-normal text-slate-400">· 05</span>
        </h2>
        <p className="mt-2 text-sm text-slate-500">
          This patient has active insurance coverage. A Good Faith Estimate under the No Surprises
          Act is for uninsured / self-pay individuals, so 05 does not apply here.
        </p>
      </section>
    );
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch<CostEstimateResult>(
        `/api/appointments/${appointmentId}/cost-estimate`,
        { method: "POST", body: JSON.stringify({ procedure_codes: picked, self_pay: selfPay }) },
      );
      setResult(r);
      setExisting(r.cost_estimate);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-semibold text-slate-700">
        Cost estimate{" "}
        <span className="font-normal text-slate-400">
          · 05, No Surprises Act Good Faith Estimate — self-pay only
        </span>
      </h2>

      {error && <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {existing && (
        <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          An estimate for {money(existing.subtotal)} was generated{" "}
          {new Date(existing.created_at).toLocaleDateString()}.{" "}
          <Link to={`/app/cost-estimates/${existing.id}`} className="font-medium underline">
            Open the document
          </Link>
          .
        </p>
      )}

      {result && result.unpriced_codes.length > 0 && (
        <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700">
          No price on file for {result.unpriced_codes.join(", ")} — add it under Settings. These
          codes were left off the estimate.
        </p>
      )}

      {prices === null ? (
        <p className="mt-3 text-sm text-slate-400">Loading price list…</p>
      ) : prices.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          No procedure prices on file. Run <code>scripts/seed_procedure_prices.py</code> or add them
          under Settings.
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          <p className="text-xs text-slate-500">Select the services planned for this visit:</p>
          <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2">
            {prices.map((p) => {
              const checked = picked.includes(p.procedure_code);
              return (
                <label
                  key={p.id}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      setPicked((cur) =>
                        checked
                          ? cur.filter((c) => c !== p.procedure_code)
                          : [...cur, p.procedure_code],
                      )
                    }
                  />
                  <span className="flex-1 text-slate-700">
                    {p.description}{" "}
                    <span className="text-xs text-slate-400">({p.procedure_code})</span>
                  </span>
                  <span className="tabular-nums text-slate-500">{money(p.base_price)}</span>
                </label>
              );
            })}
          </div>

          {picked.length > 0 && (
            <p className="text-sm text-slate-600">
              Estimated total: <strong className="tabular-nums">{money(subtotalPreview)}</strong>
            </p>
          )}

          <label className="flex items-start gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={selfPay}
              onChange={(e) => setSelfPay(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              I confirm this is a <strong>self-pay</strong> encounter — the patient is uninsured or
              has chosen not to use insurance for this visit.
            </span>
          </label>

          <button
            onClick={() => void generate()}
            disabled={busy || picked.length === 0 || !selfPay}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {busy ? "Generating…" : "Generate Good Faith Estimate"}
          </button>
          {!selfPay && picked.length > 0 && (
            <p className="text-xs text-amber-600">
              Confirm the self-pay affirmation above to generate the estimate.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
