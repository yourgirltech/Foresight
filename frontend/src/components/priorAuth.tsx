import type { PriorAuthStatus } from "../lib/types";

const STATUS_LABEL: Record<PriorAuthStatus, string> = {
  pending: "Checking…",
  not_required: "No auth needed",
  emergency_exempt: "Not required — emergency",
  insufficient_info: "Needs more info",
  required_draft: "Awaiting your approval",
  submission_declined: "Submission declined",
  submitting: "Submitting…",
  submitted: "Submitted — awaiting payer",
  auth_approved: "Authorized",
  info_needed: "Payer needs more info",
  auth_denied: "Denied",
};

// not_required / emergency_exempt are INFORMATION, not errors.
const STATUS_TONE: Record<PriorAuthStatus, string> = {
  pending: "bg-slate-100 text-slate-600",
  not_required: "bg-emerald-50 text-emerald-700",
  emergency_exempt: "bg-emerald-50 text-emerald-700",
  insufficient_info: "bg-violet-50 text-violet-700",
  required_draft: "bg-amber-50 text-amber-700",
  submission_declined: "bg-slate-100 text-slate-500",
  submitting: "bg-sky-50 text-sky-700",
  submitted: "bg-sky-50 text-sky-700",
  auth_approved: "bg-emerald-50 text-emerald-700",
  info_needed: "bg-violet-50 text-violet-700",
  auth_denied: "bg-red-50 text-red-700",
};

export function priorAuthLabel(s: PriorAuthStatus): string {
  return STATUS_LABEL[s] ?? s;
}

export function PriorAuthBadge({ status }: { status: PriorAuthStatus | null | undefined }) {
  if (!status) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
        STATUS_TONE[status] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {priorAuthLabel(status)}
    </span>
  );
}

/** Makes the non-gating principle visible in the product, not just the code. */
export function PriorAuthEmergencyPill() {
  return (
    <span className="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-600/20">
      Emergency — prior auth does not apply
    </span>
  );
}

export const NEEDS_ACTION: ReadonlySet<PriorAuthStatus> = new Set<PriorAuthStatus>([
  "required_draft",
  "auth_denied",
  "info_needed",
]);
