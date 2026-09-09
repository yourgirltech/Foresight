import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { VoiceReminderBadge, VR_NEEDS_ACTION } from "../components/voiceReminder";
import { apiFetch } from "../lib/api";
import type { VoiceReminder, VoiceReminderListResponse, VoiceReminderStatus } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

const IN_FLIGHT: VoiceReminderStatus[] = ["pending", "dispatching", "calling"];

function Row({ vr }: { vr: VoiceReminder }) {
  const v = vr.variable_values ?? {};
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-3 text-slate-700">
        <Link
          to={`/app/appointments/${vr.appointment_id}`}
          className="font-medium text-brand-700 hover:underline"
        >
          {vr.patient_name_snapshot}
        </Link>
        <div className="text-xs text-slate-400">{vr.patient_phone_snapshot}</div>
      </td>
      <td className="px-4 py-3 text-slate-600">
        {v.appointment_date ? (
          <>
            {v.appointment_date}
            {v.appointment_time ? `, ${v.appointment_time}` : ""}
          </>
        ) : (
          when(vr.appointment_at_snapshot)
        )}
      </td>
      <td className="px-4 py-3 text-slate-500">
        {vr.placed_at ? `called ${when(vr.placed_at)}` : `due ${when(vr.scheduled_call_at)}`}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <VoiceReminderBadge status={vr.status} />
          {vr.escalation_id && <span className="text-xs text-red-600">escalated</span>}
        </div>
      </td>
    </tr>
  );
}

function Table({ rows, empty }: { rows: VoiceReminder[]; empty: string }) {
  return (
    <table className="min-w-full divide-y divide-slate-200 text-sm">
      <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
        <tr>
          <th className="px-4 py-3">Patient</th>
          <th className="px-4 py-3">Appointment</th>
          <th className="px-4 py-3">Call</th>
          <th className="px-4 py-3">Status</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {rows.length === 0 && (
          <tr>
            <td colSpan={4} className="px-4 py-6 text-center text-slate-400">
              {empty}
            </td>
          </tr>
        )}
        {rows.map((vr) => (
          <Row key={vr.id} vr={vr} />
        ))}
      </tbody>
    </table>
  );
}

export function RemindersPage() {
  const { organization } = useAuth();
  const [data, setData] = useState<VoiceReminderListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<VoiceReminderListResponse>("/api/voice-reminders")
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  const all = data?.voice_reminders ?? [];
  const needsAction = all.filter((v) => VR_NEEDS_ACTION.has(v.status));
  const confirmed = all.filter((v) => v.status === "confirmed");
  const inFlight = all.filter((v) => IN_FLIGHT.includes(v.status));
  const cancelled = all.filter((v) => v.status === "cancelled");

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Voice reminders</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · automated appointment-reminder calls placed by agent 17. A call is
          never placed without a TCPA voice-consent record and a staff member&rsquo;s authorization;
          anything other than a clean confirmation is routed to a human.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {data === null && !error && <p className="text-slate-400">Loading…</p>}

      {data && (
        <>
          <section className="overflow-hidden rounded-xl border border-amber-200 bg-white">
            <div className="border-b border-amber-100 bg-amber-50/50 px-4 py-3">
              <h2 className="text-sm font-semibold text-amber-900">
                Needs follow-up
                {needsAction.length > 0 && (
                  <span className="ml-2 rounded-full bg-amber-200 px-1.5 py-0.5 text-xs text-amber-800">
                    {needsAction.length}
                  </span>
                )}
              </h2>
            </div>
            <Table
              rows={needsAction}
              empty="No reminders need attention — every call confirmed or is still scheduled."
            />
          </section>

          {inFlight.length > 0 && (
            <section className="overflow-hidden rounded-xl border border-sky-200 bg-white">
              <div className="border-b border-sky-100 bg-sky-50/40 px-4 py-3">
                <h2 className="text-sm font-semibold text-sky-900">Scheduled / in progress</h2>
              </div>
              <Table rows={inFlight} empty="—" />
            </section>
          )}

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-3">
              <h2 className="text-sm font-semibold text-slate-700">
                Confirmed
                {confirmed.length > 0 && (
                  <span className="ml-2 rounded-full bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">
                    {confirmed.length}
                  </span>
                )}
              </h2>
            </div>
            <Table rows={confirmed} empty="No confirmed reminder calls yet." />
          </section>

          {cancelled.length > 0 && (
            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-4 py-3">
                <h2 className="text-sm font-semibold text-slate-500">Cancelled</h2>
              </div>
              <Table rows={cancelled} empty="—" />
            </section>
          )}
        </>
      )}
    </div>
  );
}
