import type { VoiceReminder, VoiceReminderStatus } from "../lib/types";

const LABEL: Record<VoiceReminderStatus, string> = {
  pending: "Scheduled",
  skipped_no_consent: "Skipped — no consent",
  skipped_no_phone: "Skipped — no phone",
  cancelled: "Cancelled",
  dispatching: "Dispatching…",
  calling: "Calling…",
  confirmed: "Patient confirmed",
  reschedule_requested: "Reschedule requested",
  wrong_person: "Wrong person",
  out_of_scope: "Out of scope",
  no_answer: "No answer",
  call_failed: "Call failed",
  error: "Error",
};

// confirmed is the only clean terminal; skips/failures need a human.
const TONE: Record<VoiceReminderStatus, string> = {
  pending: "bg-sky-50 text-sky-700",
  skipped_no_consent: "bg-amber-50 text-amber-700",
  skipped_no_phone: "bg-amber-50 text-amber-700",
  cancelled: "bg-slate-100 text-slate-500",
  dispatching: "bg-sky-50 text-sky-700",
  calling: "bg-sky-50 text-sky-700",
  confirmed: "bg-emerald-50 text-emerald-700",
  reschedule_requested: "bg-violet-50 text-violet-700",
  wrong_person: "bg-red-50 text-red-700",
  out_of_scope: "bg-violet-50 text-violet-700",
  no_answer: "bg-amber-50 text-amber-700",
  call_failed: "bg-red-50 text-red-700",
  error: "bg-red-50 text-red-700",
};

export function voiceReminderLabel(s: VoiceReminderStatus): string {
  return LABEL[s] ?? s;
}

/** statuses that put a reminder on the "needs a human" list */
export const VR_NEEDS_ACTION: ReadonlySet<VoiceReminderStatus> = new Set<VoiceReminderStatus>([
  "skipped_no_consent",
  "skipped_no_phone",
  "reschedule_requested",
  "wrong_person",
  "out_of_scope",
  "no_answer",
  "call_failed",
  "error",
]);

export function VoiceReminderBadge({ status }: { status: VoiceReminderStatus | null | undefined }) {
  if (!status) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
        TONE[status] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {voiceReminderLabel(status)}
    </span>
  );
}

function when(ts: string | null | undefined): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

function Attempt({ vr, index }: { vr: VoiceReminder; index: number }) {
  const v = vr.variable_values ?? {};
  const p = (vr.outcome_payload ?? {}) as Record<string, unknown>;
  return (
    <li className="rounded-lg border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-slate-400">Attempt {index + 1}</span>
        <VoiceReminderBadge status={vr.status} />
        {vr.escalation_id && (
          <span className="text-xs text-red-600">→ escalated for a human</span>
        )}
      </div>
      <dl className="mt-2 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-xs text-slate-600">
        <dt className="text-slate-400">Dials</dt>
        <dd>{vr.patient_phone_snapshot}</dd>
        <dt className="text-slate-400">Would say</dt>
        <dd>
          {v.clinic_name ? (
            <>
              &ldquo;{v.clinic_name}&rdquo; · {v.patient_name} · {v.appointment_date}
              {v.appointment_time ? `, ${v.appointment_time}` : ""}
            </>
          ) : (
            <span className="text-slate-400">not resolved</span>
          )}
        </dd>
        <dt className="text-slate-400">Scheduled</dt>
        <dd>{when(vr.scheduled_call_at)}</dd>
        {vr.placed_at && (
          <>
            <dt className="text-slate-400">Placed</dt>
            <dd>{when(vr.placed_at)}</dd>
          </>
        )}
        {vr.completed_at && (
          <>
            <dt className="text-slate-400">Completed</dt>
            <dd>{when(vr.completed_at)}</dd>
          </>
        )}
        {vr.outcome && (
          <>
            <dt className="text-slate-400">Outcome</dt>
            <dd>{vr.outcome}</dd>
          </>
        )}
        {typeof p.ended_reason === "string" && (
          <>
            <dt className="text-slate-400">Ended</dt>
            <dd>{p.ended_reason}</dd>
          </>
        )}
        {typeof p.duration_seconds === "number" && (
          <>
            <dt className="text-slate-400">Duration</dt>
            <dd>{p.duration_seconds}s</dd>
          </>
        )}
        <dt className="text-slate-400">Consent</dt>
        <dd>
          {vr.consent_snapshot ? "granted" : "not granted"}
          {vr.consent_source_snapshot ? ` · ${vr.consent_source_snapshot}` : ""}
        </dd>
        {vr.vapi_call_id && (
          <>
            <dt className="text-slate-400">Vapi call</dt>
            <dd className="font-mono">{vr.vapi_call_id}</dd>
          </>
        )}
      </dl>
    </li>
  );
}

/** The Voice Reminder section on the appointment detail page. */
export function VoiceReminderCard({ reminders }: { reminders: VoiceReminder[] }) {
  const latest = reminders[reminders.length - 1] ?? null;
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">
          Voice reminder{" "}
          <span className="font-normal text-slate-400">
            · 17, automated call — never sent without consent + a human&rsquo;s authorization
          </span>
        </h2>
        <a href="/app/reminders" className="text-xs font-medium text-brand-600 hover:underline">
          All reminders →
        </a>
      </div>

      {!latest ? (
        <p className="mt-3 text-sm text-slate-500">
          No reminder call for this appointment. Staff enroll one from the patient&rsquo;s contact
          once TCPA consent is on file.
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <VoiceReminderBadge status={latest.status} />
            {latest.status === "confirmed" && latest.completed_at && (
              <span className="text-xs text-emerald-700">
                confirmed the appointment on {when(latest.completed_at)}
              </span>
            )}
            {latest.escalation_id && (
              <span className="text-xs text-red-600">a human has been notified</span>
            )}
          </div>
          <ol className="mt-3 space-y-2">
            {reminders.map((vr, i) => (
              <Attempt key={vr.id} vr={vr} index={i} />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
