import type { ApprovalStatus, ClaimStatus, IssueSeverity, RiskLevel } from "../lib/types";

const STATUS_LABEL: Record<ClaimStatus, string> = {
  received: "Received",
  analyzed: "Analyzed",
  cleared: "Cleared — no issues",
  reasoned: "Reasoned",
  awaiting_approval: "Awaiting approval",
  executing: "Executing",
  actioned: "Actioned",
  manual_action_required: "Approved — manual action required",
  declined: "Declined",
  escalated: "Escalated",
  denied: "Denied (payer)",
  paid: "Paid (payer)",
  rejected: "Rejected (payer)",
};

const STATUS_TONE: Record<ClaimStatus, string> = {
  received: "bg-slate-100 text-slate-600",
  analyzed: "bg-slate-100 text-slate-600",
  cleared: "bg-emerald-50 text-emerald-700",
  reasoned: "bg-slate-100 text-slate-600",
  awaiting_approval: "bg-amber-50 text-amber-700",
  executing: "bg-blue-50 text-blue-700",
  actioned: "bg-emerald-50 text-emerald-700",
  manual_action_required: "bg-violet-50 text-violet-700",
  declined: "bg-slate-100 text-slate-600",
  escalated: "bg-red-50 text-red-700",
  denied: "bg-red-50 text-red-700",
  paid: "bg-emerald-50 text-emerald-700",
  rejected: "bg-red-50 text-red-700",
};

export function statusLabel(s: ClaimStatus): string {
  return STATUS_LABEL[s] ?? s;
}

export function StatusBadge({ status }: { status: ClaimStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_TONE[status] ?? "bg-slate-100 text-slate-600"}`}
    >
      {statusLabel(status)}
    </span>
  );
}

const RISK_TONE: Record<RiskLevel, string> = {
  Low: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  Medium: "bg-amber-50 text-amber-700 ring-amber-600/20",
  High: "bg-red-50 text-red-700 ring-red-600/20",
};

export function RiskBadge({ level, score }: { level: RiskLevel | null; score?: number }) {
  if (!level) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${RISK_TONE[level]}`}
    >
      {level}
      {typeof score === "number" && <span className="font-normal opacity-70">{score}</span>}
    </span>
  );
}

const SEVERITY_TONE: Record<IssueSeverity, string> = {
  low: "bg-slate-100 text-slate-600",
  medium: "bg-amber-50 text-amber-700",
  high: "bg-red-50 text-red-700",
};

export function SeverityBadge({ severity }: { severity: IssueSeverity }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${SEVERITY_TONE[severity]}`}>
      {severity}
    </span>
  );
}

const APPROVAL_TONE: Record<ApprovalStatus, string> = {
  pending: "bg-amber-50 text-amber-700",
  approved: "bg-emerald-50 text-emerald-700",
  declined: "bg-slate-100 text-slate-600",
};

export function ApprovalBadge({ status }: { status: ApprovalStatus }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${APPROVAL_TONE[status]}`}>
      {status}
    </span>
  );
}

export function prettyIssueType(t: string): string {
  return t.replace(/_/g, " ");
}

export function prettyActor(actor: string): string {
  if (actor.startsWith("human:")) return "Human";
  const map: Record<string, string> = {
    "00-commander": "Commander",
    "01-eligibility": "Eligibility (01)",
    "06-analyzer": "Analyzer (06)",
    "07-reasoning": "Reasoning (07)",
    "08-recommendation": "Recommendation (08)",
    "09-followup": "Follow-up agent (09)",
    "10-reminder": "Reminder agent (10)",
    "12-escalation": "Escalation (12)",
  };
  return map[actor] ?? actor;
}
