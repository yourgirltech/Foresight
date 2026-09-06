import type { EligibilityCheck, EligibilityStatus } from "../lib/types";

const STATUS_LABEL: Record<EligibilityStatus, string> = {
  pending: "Checking…",
  verified_active: "Active coverage",
  verified_inactive: "Inactive coverage",
  insufficient_info: "Needs more info",
  check_failed: "Check failed",
};

// inactive coverage is INFORMATION, not an error — amber, not red.
const STATUS_TONE: Record<EligibilityStatus, string> = {
  pending: "bg-slate-100 text-slate-600",
  verified_active: "bg-emerald-50 text-emerald-700",
  verified_inactive: "bg-amber-50 text-amber-700",
  insufficient_info: "bg-violet-50 text-violet-700",
  check_failed: "bg-red-50 text-red-700",
};

export function eligibilityLabel(s: EligibilityStatus): string {
  return STATUS_LABEL[s] ?? s;
}

export function EligibilityBadge({ status }: { status: EligibilityStatus | null | undefined }) {
  if (!status) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
        STATUS_TONE[status] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {eligibilityLabel(status)}
    </span>
  );
}

/** Makes the non-blocking principle visible in the product, not just the code. */
export function EmergencyPill() {
  return (
    <span className="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-600/20">
      Emergency — care not gated
    </span>
  );
}

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

/** The append-only re-check history for one patient/appointment, oldest first. */
export function CheckHistory({ checks }: { checks: EligibilityCheck[] }) {
  if (checks.length === 0) {
    return <p className="text-sm text-slate-500">No eligibility check yet.</p>;
  }
  return (
    <ol className="space-y-3">
      {checks.map((c, i) => {
        const p = c.result_payload ?? {};
        return (
          <li key={c.id} className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-slate-400">#{i + 1}</span>
              <EligibilityBadge status={c.status} />
              <span className="text-xs text-slate-400">checked {when(c.checked_at)}</span>
              {c.is_emergency && <EmergencyPill />}
            </div>
            {typeof p.reason === "string" && (
              <p className="mt-2 text-sm text-slate-700">{p.reason}</p>
            )}
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-500 sm:grid-cols-4">
              <div>
                <dt className="text-slate-400">Member ID</dt>
                <dd className="tabular-nums text-slate-600">{c.patient_member_id || "—"}</dd>
              </div>
              <div>
                <dt className="text-slate-400">Payer</dt>
                <dd className="text-slate-600">{c.payer_name ?? "—"}</dd>
              </div>
              {typeof p.member_bucket === "number" && (
                <div>
                  <dt className="text-slate-400">Bucket / threshold</dt>
                  <dd className="tabular-nums text-slate-600">
                    {p.member_bucket} / {String(p.active_threshold ?? "—")}
                  </dd>
                </div>
              )}
              {p.recheck_recommended === true && (
                <div>
                  <dt className="text-slate-400">Follow-up</dt>
                  <dd className="text-violet-700">re-check when more info exists</dd>
                </div>
              )}
            </dl>
          </li>
        );
      })}
    </ol>
  );
}
