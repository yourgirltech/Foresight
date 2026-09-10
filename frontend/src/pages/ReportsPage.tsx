import { useEffect, useState } from "react";

import { useAuth } from "../auth/useAuth";
import { apiFetch } from "../lib/api";
import type { ReportsResponse } from "../lib/types";

function Stat({
  label,
  value,
  sub,
  tone = "slate",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "slate" | "emerald" | "red" | "amber";
}) {
  const toneCls = {
    slate: "text-slate-900",
    emerald: "text-emerald-700",
    red: "text-red-700",
    amber: "text-amber-700",
  }[tone];
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${toneCls}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function pct(v: number | null): string {
  return v === null || v === undefined ? "—" : `${v}%`;
}
function money(v: number): string {
  return `$${v.toLocaleString()}`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
    </section>
  );
}

export function ReportsPage() {
  const { organization } = useAuth();
  const [d, setD] = useState<ReportsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<ReportsResponse>("/api/reports").then(setD).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Reports</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · revenue-cycle KPIs and automation impact across every claim,
          eligibility check, prior auth, appeal, and reminder call in the system. Real counts only.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {d === null && !error && <p className="text-slate-400">Loading…</p>}

      {d && (
        <>
          <Section title="Claims">
            <Stat label="Total claims" value={String(d.claims.total)} />
            <Stat
              label="Clean rate"
              value={pct(d.claims.clean_rate_pct)}
              sub="low-risk on first pass"
              tone="emerald"
            />
            <Stat
              label="Denial rate"
              value={pct(d.claims.denial_rate_pct)}
              sub={money(d.claims.denied_amount) + " denied"}
              tone={d.claims.denial_rate_pct && d.claims.denial_rate_pct > 10 ? "red" : "slate"}
            />
            <Stat
              label="Billed / paid"
              value={money(d.claims.billed_amount)}
              sub={money(d.claims.paid_amount) + " paid"}
            />
          </Section>

          <Section title="Automation impact">
            <Stat
              label="Automation rate"
              value={pct(d.automation.automation_rate_pct)}
              sub="agent-executed vs. escalated"
              tone="emerald"
            />
            <Stat label="Agent actions executed" value={String(d.automation.agent_actions_executed)} />
            <Stat
              label="Escalated to a human"
              value={String(d.automation.escalated_to_human)}
              tone="amber"
            />
            <Stat label="Human approvals" value={String(d.automation.human_approvals)} />
          </Section>

          <Section title="Prior authorization">
            <Stat label="Submitted" value={String(d.prior_auth.submitted)} />
            <Stat
              label="Approval rate"
              value={pct(d.prior_auth.approval_rate_pct)}
              tone="emerald"
            />
            <Stat label="No auth required" value={String(d.prior_auth.not_required)} />
            <Stat label="Emergency exempt" value={String(d.prior_auth.emergency_exempt)} />
          </Section>

          <Section title="Appeals">
            <Stat label="Resolved" value={String(d.appeals.resolved)} />
            <Stat label="Win rate" value={pct(d.appeals.win_rate_pct)} tone="emerald" />
            <Stat label="Partial reversals" value={String(d.appeals.partial)} />
            <Stat label="Upheld" value={String(d.appeals.upheld)} tone="amber" />
          </Section>

          <Section title="Voice reminders">
            <Stat label="Calls completed" value={String(d.voice_reminders.completed)} />
            <Stat
              label="Confirm rate"
              value={pct(d.voice_reminders.confirm_rate_pct)}
              sub={`${d.voice_reminders.confirmed} confirmed`}
              tone="emerald"
            />
            <Stat
              label="Needs follow-up"
              value={String(
                d.voice_reminders.completed - d.voice_reminders.confirmed,
              )}
              tone="amber"
            />
            <Stat
              label="Eligibility checks"
              value={String(Object.values(d.eligibility).reduce((a, b) => a + b, 0))}
            />
          </Section>

          <p className="text-xs text-slate-400">
            Not yet tracked: time-saved estimates, per-staff throughput, trend-over-time charts —
            those need a metrics store Foresight hasn&rsquo;t built.
          </p>
        </>
      )}
    </div>
  );
}
