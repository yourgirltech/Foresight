import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CheckHistory, EmergencyPill } from "../components/eligibility";
import { InsuranceSummary } from "../components/cob";
import { CostEstimateSection } from "../components/costEstimate";
import { PriorAuthBadge } from "../components/priorAuth";
import { VoiceReminderCard } from "../components/voiceReminder";
import { apiFetch } from "../lib/api";
import { prettyActor } from "../components/claims";
import type { AppointmentDetail, EligibilityDecisionResult } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

export function AppointmentDetailPage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<AppointmentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memberId, setMemberId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<AppointmentDetail>(`/api/appointments/${id}`));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !data) {
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

  const { appointment, payer, checks, prior_authorizations, activity_log, coverages, cob, voice_reminders } =
    data;
  const latest = checks[checks.length - 1] ?? null;

  async function recheck() {
    if (!latest) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await apiFetch<EligibilityDecisionResult>(
        `/api/eligibility-checks/${latest.id}/recheck`,
        { method: "POST", body: JSON.stringify(memberId ? { patient_member_id: memberId } : {}) },
      );
      setNotice(`Re-checked — Commander: ${r.decision.reason_code}`);
      setMemberId("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Link to="/appointments" className="text-sm text-brand-600 hover:underline">
        ← Appointments
      </Link>

      <header className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{appointment.patient_name}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {appointment.is_emergency ? "Emergency registration" : `Scheduled ${when(appointment.scheduled_at)}`}
              {" · "}member {appointment.patient_member_id || "—"} · {payer?.name ?? "no payer on file"}
            </p>
          </div>
          {appointment.is_emergency && <EmergencyPill />}
        </div>
        {payer && !payer.eligibility_verification_supported && (
          <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700">
            {payer.name} is not on the real-time eligibility network — checks against it return
            &ldquo;check failed&rdquo; and (for a scheduled visit) become a staff re-verify task.
          </p>
        )}
      </header>

      {notice && (
        <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</p>
      )}
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">
          Eligibility checks <span className="font-normal text-slate-400">· 01, append-only history</span>
        </h2>
        <div className="mt-3">
          <CheckHistory checks={checks} />
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-slate-100 pt-4">
          <label className="text-xs text-slate-500">
            Corrected member ID (optional)
            <input
              value={memberId}
              onChange={(e) => setMemberId(e.target.value)}
              placeholder={latest?.patient_member_id || "e.g. BX412900037"}
              className="mt-1 block rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <button
            onClick={() => void recheck()}
            disabled={busy || !latest}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
          >
            {busy ? "Re-checking…" : "Re-check eligibility"}
          </button>
        </div>
      </section>

      <InsuranceSummary
        patientName={appointment.patient_name}
        patientDob={appointment.patient_dob}
        appointmentId={appointment.id}
        coverages={coverages}
        cob={cob}
        onChanged={load}
      />

      <CostEstimateSection appointmentId={appointment.id} coverages={coverages} />

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">
            Prior authorization{" "}
            <span className="font-normal text-slate-400">· 02, informs staff — never gates the visit</span>
          </h2>
          <Link to="/app/prior-auth" className="text-xs font-medium text-brand-600 hover:underline">
            All prior auth →
          </Link>
        </div>
        {prior_authorizations.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">
            No prior-authorization check for this appointment.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {prior_authorizations.map((pa) => (
              <li key={pa.id} className="flex flex-wrap items-center gap-2 text-sm">
                <Link
                  to={`/app/prior-auth/${pa.id}`}
                  className="font-medium text-brand-700 hover:underline"
                >
                  {pa.procedure_code || "no code"}
                </Link>
                {pa.procedure_description && (
                  <span className="text-xs text-slate-400">{pa.procedure_description}</span>
                )}
                <PriorAuthBadge status={pa.status} />
                {pa.status === "emergency_exempt" && (
                  <span className="text-xs text-emerald-700">
                    not required — emergency exemption
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <VoiceReminderCard reminders={voice_reminders} />

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">Activity</h2>
        <ol className="mt-3 space-y-2">
          {activity_log.map((a, n) => (
            <li key={n} className="flex gap-3 text-sm">
              <span className="w-40 shrink-0 text-xs text-slate-400">{when(a.created_at)}</span>
              <span className="w-36 shrink-0 font-medium text-slate-600">{prettyActor(a.actor)}</span>
              <span className="text-slate-700">{a.action}</span>
            </li>
          ))}
          {activity_log.length === 0 && <li className="text-sm text-slate-400">No activity yet.</li>}
        </ol>
      </section>
    </div>
  );
}
