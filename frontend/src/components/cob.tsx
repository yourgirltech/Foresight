import { useMemo, useState, type ReactNode } from "react";

import { apiFetch } from "../lib/api";
import type {
  CobPlacement,
  CoverageRelationship,
  CoverageType,
  PatientCoverage,
} from "../lib/types";

const ORDER_TONE: Record<string, string> = {
  primary: "bg-emerald-50 text-emerald-700",
  secondary: "bg-sky-50 text-sky-700",
  tertiary: "bg-violet-50 text-violet-700",
};

const COVERAGE_TYPE_LABEL: Record<CoverageType, string> = {
  employer_active: "Employer (active)",
  employer_retiree: "Employer (retiree)",
  cobra: "COBRA",
  individual: "Individual / marketplace",
  medicare: "Medicare",
  medicaid: "Medicaid",
  tricare: "TRICARE",
  other: "Other",
};

const RELATIONSHIPS: CoverageRelationship[] = ["self", "spouse", "child", "other"];

export function CobBadge({ order }: { order?: string }) {
  if (!order) return <span className="text-xs text-slate-400">not ranked</span>;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${
        ORDER_TONE[order] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {order}
    </span>
  );
}

function isActive(c: PatientCoverage, today: string): boolean {
  if (!c.effective_date || c.effective_date > today) return false;
  return !c.termination_date || c.termination_date >= today;
}

export function InsuranceSummary({
  patientName,
  patientDob,
  appointmentId,
  coverages,
  cob,
  onChanged,
}: {
  patientName: string;
  patientDob: string | null;
  appointmentId: string;
  coverages: PatientCoverage[];
  cob: Record<string, CobPlacement[]>;
  onChanged: () => void | Promise<void>;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  const placementByCoverage = useMemo(() => {
    const map: Record<string, CobPlacement> = {};
    for (const list of Object.values(cob)) {
      for (const p of list) map[p.coverage_id] = p;
    }
    return map;
  }, [cob]);

  const byKind = useMemo(() => {
    const groups: Record<string, PatientCoverage[]> = {};
    for (const c of coverages) (groups[c.plan_kind] ??= []).push(c);
    for (const list of Object.values(groups)) {
      list.sort((a, b) => {
        const oa = placementByCoverage[a.id]?.order ?? "zzz";
        const ob = placementByCoverage[b.id]?.order ?? "zzz";
        return oa.localeCompare(ob);
      });
    }
    return groups;
  }, [coverages, placementByCoverage]);

  async function mutate(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const pin = (id: string, position: number) =>
    mutate(() =>
      apiFetch(`/api/coverages/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ manual_order_override: position }),
      }),
    );
  const clearPin = (id: string) =>
    mutate(() =>
      apiFetch(`/api/coverages/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ clear_override: true }),
      }),
    );
  const remove = (id: string) =>
    mutate(() => apiFetch(`/api/coverages/${id}`, { method: "DELETE" }));

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">
          Insurance summary{" "}
          <span className="font-normal text-slate-400">
            · 04, deterministic — every ordering shows the NAIC rule that decided it
          </span>
        </h2>
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="text-xs font-medium text-brand-600 hover:underline"
        >
          {showAdd ? "Cancel" : "Add coverage"}
        </button>
      </div>

      {error && <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {showAdd && (
        <AddCoverageForm
          patientName={patientName}
          patientDob={patientDob}
          appointmentId={appointmentId}
          busy={busy}
          onSubmit={(body) =>
            mutate(async () => {
              await apiFetch("/api/coverages", { method: "POST", body: JSON.stringify(body) });
              setShowAdd(false);
            })
          }
        />
      )}

      {coverages.length === 0 && !showAdd && (
        <p className="mt-3 text-sm text-slate-500">
          No coverages recorded. Add one above — coordination of benefits only matters with two or
          more active plans.
        </p>
      )}

      {Object.entries(byKind).map(([kind, list]) => (
        <div key={kind} className="mt-4">
          {Object.keys(byKind).length > 1 && (
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{kind}</p>
          )}
          <ul className="mt-2 divide-y divide-slate-100">
            {list.map((c) => {
              const p = placementByCoverage[c.id];
              const active = isActive(c, today);
              return (
                <li key={c.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-800">{c.payer_name}</span>
                    <span className="text-xs text-slate-400">
                      {COVERAGE_TYPE_LABEL[c.coverage_type]} · member {c.member_id || "—"}
                      {c.is_dependent ? " · as dependent" : ""}
                    </span>
                    {active ? (
                      <CobBadge order={p?.order} />
                    ) : (
                      <span className="text-xs text-slate-400">inactive — not ranked</span>
                    )}
                    {c.manual_order_override != null && (
                      <span className="text-xs text-amber-600">pinned</span>
                    )}
                  </div>
                  {active && p && (
                    <p className="mt-1 text-xs text-slate-500">{p.rationale}</p>
                  )}
                  <div className="mt-1.5 flex items-center gap-2 text-xs">
                    {active && (
                      <>
                        <span className="text-slate-400">Pin as</span>
                        {[1, 2, 3].map((n) => (
                          <button
                            key={n}
                            disabled={busy}
                            onClick={() => pin(c.id, n)}
                            className={`rounded border px-1.5 py-0.5 ${
                              c.manual_order_override === n
                                ? "border-amber-400 bg-amber-50 text-amber-700"
                                : "border-slate-200 text-slate-500 hover:bg-slate-50"
                            }`}
                          >
                            {n}
                          </button>
                        ))}
                        {c.manual_order_override != null && (
                          <button
                            disabled={busy}
                            onClick={() => clearPin(c.id)}
                            className="text-slate-400 hover:text-slate-600 hover:underline"
                          >
                            clear
                          </button>
                        )}
                      </>
                    )}
                    <button
                      disabled={busy}
                      onClick={() => remove(c.id)}
                      className="ml-auto text-slate-400 hover:text-red-600 hover:underline"
                    >
                      remove
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}

interface AddBody {
  patient_name: string;
  patient_dob: string | null;
  appointment_id: string;
  payer_name: string;
  member_id: string;
  plan_kind: string;
  coverage_type: CoverageType;
  relationship_to_subscriber: CoverageRelationship;
  is_dependent: boolean;
  subscriber_name: string;
  subscriber_dob: string | null;
  effective_date: string;
}

function AddCoverageForm({
  patientName,
  patientDob,
  appointmentId,
  busy,
  onSubmit,
}: {
  patientName: string;
  patientDob: string | null;
  appointmentId: string;
  busy: boolean;
  onSubmit: (body: AddBody) => void;
}) {
  const [payerName, setPayerName] = useState("");
  const [memberId, setMemberId] = useState("");
  const [planKind, setPlanKind] = useState("medical");
  const [coverageType, setCoverageType] = useState<CoverageType>("employer_active");
  const [relationship, setRelationship] = useState<CoverageRelationship>("self");
  const [subscriberName, setSubscriberName] = useState("");
  const [subscriberDob, setSubscriberDob] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");

  const isDependent = relationship !== "self";

  return (
    <form
      className="mt-3 grid gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          patient_name: patientName,
          patient_dob: patientDob,
          appointment_id: appointmentId,
          payer_name: payerName.trim(),
          member_id: memberId.trim(),
          plan_kind: planKind,
          coverage_type: coverageType,
          relationship_to_subscriber: relationship,
          is_dependent: isDependent,
          subscriber_name: isDependent ? subscriberName.trim() : patientName,
          subscriber_dob: subscriberDob || null,
          effective_date: effectiveDate,
        });
      }}
    >
      <Field label="Payer / insurer">
        <input required value={payerName} onChange={(e) => setPayerName(e.target.value)} className={inputCls} />
      </Field>
      <Field label="Member ID">
        <input value={memberId} onChange={(e) => setMemberId(e.target.value)} className={inputCls} />
      </Field>
      <Field label="Plan kind">
        <select value={planKind} onChange={(e) => setPlanKind(e.target.value)} className={inputCls}>
          {["medical", "dental", "vision", "rx"].map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Coverage type">
        <select
          value={coverageType}
          onChange={(e) => setCoverageType(e.target.value as CoverageType)}
          className={inputCls}
        >
          {Object.entries(COVERAGE_TYPE_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Patient is the…">
        <select
          value={relationship}
          onChange={(e) => setRelationship(e.target.value as CoverageRelationship)}
          className={inputCls}
        >
          {RELATIONSHIPS.map((r) => (
            <option key={r} value={r}>
              {r === "self" ? "subscriber (self)" : `${r} of the subscriber`}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Effective date">
        <input
          required
          type="date"
          value={effectiveDate}
          onChange={(e) => setEffectiveDate(e.target.value)}
          className={inputCls}
        />
      </Field>
      {isDependent && (
        <>
          <Field label="Subscriber name">
            <input
              value={subscriberName}
              onChange={(e) => setSubscriberName(e.target.value)}
              className={inputCls}
            />
          </Field>
          <Field label="Subscriber date of birth (for the birthday rule)">
            <input
              type="date"
              value={subscriberDob}
              onChange={(e) => setSubscriberDob(e.target.value)}
              className={inputCls}
            />
          </Field>
        </>
      )}
      <div className="sm:col-span-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {busy ? "Saving…" : "Add coverage"}
        </button>
      </div>
    </form>
  );
}

const inputCls =
  "mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-800 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs font-medium text-slate-500">
      {label}
      {children}
    </label>
  );
}
