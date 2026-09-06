import {
  ArrowRight,
  Check,
  CheckCircle2,
  Clock,
  FileText,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";

/**
 * Small, decorative, theme-aware product visuals for the landing page's
 * capability showcase. Not interactive — they illustrate the real UI's shape
 * using the same tokens as the app.
 */

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="w-full rounded-2xl border border-border bg-surface p-5 shadow-[0_20px_50px_-24px_rgb(var(--color-text-primary)/0.25)]">
      {children}
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border bg-surface-muted/60 px-3 py-2.5">
      {children}
    </div>
  );
}

const dot = (tone: string) => <span className={`h-2 w-2 shrink-0 rounded-full ${tone}`} />;

/* ---------------- claims risk ---------------- */
export function ClaimsRiskVisual() {
  return (
    <Frame>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-text-secondary">Claim</p>
          <p className="font-mono text-sm font-medium text-text-primary">CLM-2026-00417</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-md bg-danger-soft px-2 py-1 text-xs font-semibold text-danger ring-1 ring-inset ring-danger/20">
          High risk · 80
        </span>
      </div>

      <div className="mt-4 space-y-2">
        <div className="flex items-center gap-2 text-sm text-text-secondary">
          {dot("bg-danger")}
          <span className="text-text-primary">Missing prior authorization</span>
          <span className="ml-auto rounded bg-danger-soft px-1.5 py-0.5 text-[11px] font-medium text-danger">
            high
          </span>
        </div>
        <div className="flex items-center gap-2 text-sm text-text-secondary">
          {dot("bg-warning")}
          <span className="text-text-primary">Coding doesn't match the record</span>
          <span className="ml-auto rounded bg-warning-soft px-1.5 py-0.5 text-[11px] font-medium text-warning">
            medium
          </span>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-border bg-surface-muted/60 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
          Recommended · 08
        </p>
        <p className="mt-1 text-sm font-medium text-text-primary">Submit prior-authorization request</p>
        <p className="mt-0.5 text-xs text-text-secondary">Confidence: High</p>
      </div>

      <div className="mt-4 flex gap-2">
        <span className="flex-1 rounded-lg bg-text-primary px-3 py-2 text-center text-xs font-semibold text-surface">
          Approve
        </span>
        <span className="rounded-lg border border-border px-3 py-2 text-center text-xs font-semibold text-text-secondary">
          Decline
        </span>
      </div>
    </Frame>
  );
}

/* ---------------- eligibility ---------------- */
export function EligibilityVisual() {
  return (
    <Frame>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        Eligibility · before the visit
      </p>
      <div className="mt-3 space-y-2">
        <Row>
          <span className="flex items-center gap-2 text-sm text-text-primary">
            {dot("bg-success")} Ava Nguyen · Meridian Health
          </span>
          <span className="rounded-full bg-success-soft px-2 py-0.5 text-[11px] font-medium text-success">
            Active coverage
          </span>
        </Row>
        <Row>
          <span className="flex items-center gap-2 text-sm text-text-primary">
            {dot("bg-warning")} Leo Haddad · Cascade Medicaid
          </span>
          <span className="rounded-full bg-warning-soft px-2 py-0.5 text-[11px] font-medium text-warning">
            Inactive coverage
          </span>
        </Row>
        <Row>
          <span className="flex items-center gap-2 text-sm text-text-primary">
            {dot("bg-pro")} Unidentified · ER walk-in
          </span>
          <span className="rounded-full bg-pro-soft px-2 py-0.5 text-[11px] font-medium text-pro">
            Needs more info
          </span>
        </Row>
      </div>
      <div className="mt-4 flex items-start gap-2 rounded-lg bg-accent-soft/60 p-3 text-xs text-accent">
        <ShieldCheck className="mt-px h-4 w-4 shrink-0" />
        For an emergency patient, verification runs alongside care — it never gates treatment.
      </div>
    </Frame>
  );
}

/* ---------------- front desk ---------------- */
export function FrontDeskVisual() {
  const items = [
    { name: "9:00 · Mia Garcia", note: "Reminder sent", tone: "text-success", icon: CheckCircle2 },
    { name: "9:30 · Noah Kim", note: "Intake complete", tone: "text-success", icon: CheckCircle2 },
    { name: "10:15 · Owen Brooks", note: "Confirmation pending", tone: "text-text-muted", icon: Clock },
  ];
  return (
    <Frame>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        Front desk · today
      </p>
      <div className="mt-3 space-y-2">
        {items.map((it) => (
          <Row key={it.name}>
            <span className="text-sm text-text-primary">{it.name}</span>
            <span className={`flex items-center gap-1.5 text-xs ${it.tone}`}>
              <it.icon className="h-3.5 w-3.5" />
              {it.note}
            </span>
          </Row>
        ))}
      </div>
      <div className="mt-4 flex items-center gap-2 rounded-lg bg-success-soft/60 p-3 text-xs text-success">
        <Sparkles className="h-4 w-4 shrink-0" />
        18 appointment reminders sent automatically this morning.
      </div>
    </Frame>
  );
}

/* ---------------- human approval ---------------- */
export function ApprovalVisual() {
  return (
    <Frame>
      <div className="flex items-center gap-2">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent-soft text-accent">
          <FileText className="h-4 w-4" />
        </span>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
            Awaiting your approval
          </p>
          <p className="text-sm font-medium text-text-primary">Resubmit claim with corrected coding</p>
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between text-xs text-text-secondary">
          <span>AI confidence</span>
          <span className="font-medium text-text-primary">High</span>
        </div>
        <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-muted">
          <div className="h-full w-[84%] rounded-full bg-accent" />
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <span className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-text-primary px-3 py-2 text-xs font-semibold text-surface">
          <Check className="h-3.5 w-3.5" /> Approve
        </span>
        <span className="flex items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-semibold text-text-secondary">
          <X className="h-3.5 w-3.5" /> Decline
        </span>
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-[11px] text-text-muted">
        <ShieldCheck className="h-3.5 w-3.5" />
        This action resubmits to a payer — no agent ever performs it.
      </p>
    </Frame>
  );
}

/* small arrow used by the "how it works" flow */
export function FlowArrow() {
  return (
    <span className="hidden shrink-0 text-border-strong md:block">
      <ArrowRight className="h-5 w-5" />
    </span>
  );
}

/* ---------------- commander flow (how-it-works header) ---------------- */
export function CommanderFlowVisual() {
  const nodes = [
    { label: "Event", sub: "claim · visit · walk-in", tone: "bg-surface-muted text-text-secondary" },
    { label: "Commander", sub: "routes — never acts", tone: "bg-accent text-white" },
    { label: "Specialist agent", sub: "one focused job", tone: "bg-accent-soft text-accent" },
    { label: "Human approval", sub: "anything consequential", tone: "bg-text-primary text-surface" },
  ];
  return (
    <Frame>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        The path of one decision
      </p>
      <div className="mt-4 space-y-3">
        {nodes.map((n, i) => (
          <div key={n.label} className="flex items-center gap-3">
            <span
              className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg text-xs font-bold ${n.tone}`}
            >
              {i + 1}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium text-text-primary">{n.label}</p>
              <p className="text-xs text-text-secondary">{n.sub}</p>
            </div>
            {i === 3 && <CheckCircle2 className="ml-auto h-4 w-4 text-success" />}
          </div>
        ))}
      </div>
      <p className="mt-4 border-t border-border pt-3 text-[11px] text-text-muted">
        Automation runs only after step 4.
      </p>
    </Frame>
  );
}
