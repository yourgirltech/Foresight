import { useMemo } from "react";
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  Lightbulb,
  ShieldAlert,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";

import type { DashboardData } from "../../lib/types";

/**
 * Presentational Overview. Takes data as props and an optional navigate handler
 * so the SAME component renders both the live authenticated dashboard
 * (pages/DashboardPage) and the non-interactive marketing preview
 * (marketing/DashboardPreview).
 */

/* ---------------- primitives ---------------- */

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-border bg-surface ${className}`}>{children}</div>;
}

type Tone = "accent" | "success" | "warning" | "danger" | "pro";

const TONE_BADGE: Record<Tone, string> = {
  accent: "bg-accent-soft text-accent",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  pro: "bg-pro-soft text-pro",
};

interface Stat {
  label: string;
  value: string;
  icon: LucideIcon;
  tone: Tone;
  trend: string;
  trendTone: "success" | "danger" | "accent";
  placeholder?: boolean;
}

function StatCard({ stat }: { stat: Stat }) {
  const trendColor =
    stat.trendTone === "success"
      ? "text-success"
      : stat.trendTone === "danger"
        ? "text-danger"
        : "text-accent";
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between">
        <p className="text-xs font-medium text-text-secondary">{stat.label}</p>
        <span className={`grid h-8 w-8 place-items-center rounded-full ${TONE_BADGE[stat.tone]}`}>
          <stat.icon className="h-4 w-4" strokeWidth={2} />
        </span>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight text-text-primary">{stat.value}</p>
      <div className={`mt-2 flex items-center gap-1 text-xs font-medium ${trendColor}`}>
        <ArrowUpRight className="h-3.5 w-3.5" />
        <span>{stat.trend}</span>
        {stat.placeholder && (
          <span className="ml-1 rounded bg-surface-muted px-1 py-0.5 text-[10px] font-medium text-text-muted">
            sample
          </span>
        )}
      </div>
    </Card>
  );
}

interface Insight {
  tone: Tone;
  icon: LucideIcon;
  title: string;
  subtitle: string;
  to: string;
}

function InsightRow({
  insight,
  onNavigate,
}: {
  insight: Insight;
  onNavigate?: (to: string) => void;
}) {
  return (
    <button
      onClick={() => onNavigate?.(insight.to)}
      className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-surface-muted"
    >
      <span
        className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${TONE_BADGE[insight.tone]}`}
      >
        <insight.icon className="h-[18px] w-[18px]" strokeWidth={2} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-text-primary">
          {insight.title}
        </span>
        <span className="block truncate text-xs text-text-secondary">{insight.subtitle}</span>
      </span>
      <ChevronRight className="h-4 w-4 shrink-0 text-text-muted" />
    </button>
  );
}

function Donut({
  segments,
  centerValue,
  centerLabel,
}: {
  segments: { value: number; color: string }[];
  centerValue: string;
  centerLabel: string;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const R = 54;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="relative mx-auto h-40 w-40">
      <svg viewBox="0 0 140 140" className="h-full w-full -rotate-90">
        <circle cx="70" cy="70" r={R} fill="none" stroke="rgb(var(--color-surface-muted))" strokeWidth="14" />
        {segments.map((seg, i) => {
          const len = (seg.value / total) * C;
          const el = (
            <circle
              key={i}
              cx="70"
              cy="70"
              r={R}
              fill="none"
              stroke={seg.color}
              strokeWidth="14"
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
            />
          );
          offset += len;
          return el;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-semibold text-text-primary">{centerValue}</span>
        <span className="text-xs text-text-secondary">{centerLabel}</span>
      </div>
    </div>
  );
}

/* ---------------- view ---------------- */

const today = new Date().toLocaleDateString(undefined, {
  weekday: "short",
  month: "short",
  day: "numeric",
});

export function DashboardView({
  orgName,
  data,
  error,
  onNavigate,
}: {
  orgName?: string;
  data: DashboardData | null;
  error?: string | null;
  onNavigate?: (to: string) => void;
}) {
  const stats = useMemo<Stat[]>(() => {
    const c = data?.claims;
    return [
      {
        label: "Patients Today",
        value: data ? "47" : "—",
        icon: Users,
        tone: "accent",
        trend: "12% vs last week",
        trendTone: "success",
        placeholder: true,
      },
      {
        label: "Claims Submitted",
        value: c ? String(c.submitted) : "—",
        icon: FileText,
        tone: "accent",
        trend: c ? `${c.awaiting_approval} awaiting review` : "—",
        trendTone: "accent",
      },
      {
        label: "At Risk",
        value: c ? String(c.at_risk) : "—",
        icon: AlertTriangle,
        tone: "danger",
        trend: c ? `${c.escalated} escalated this week` : "—",
        trendTone: "danger",
      },
      {
        label: "Time Saved",
        value: data ? "31h" : "—",
        icon: Clock,
        tone: "success",
        trend: "18% vs last week",
        trendTone: "success",
        placeholder: true,
      },
    ];
  }, [data]);

  const insights = useMemo<Insight[]>(() => {
    const c = data?.claims;
    const e = data?.eligibility;
    return [
      {
        tone: "danger",
        icon: ShieldAlert,
        title: `${c?.missing_authorization ?? 0} claims may be denied`,
        subtitle: "Missing prior authorization",
        to: "/app/claims",
      },
      {
        tone: "accent",
        icon: Users,
        title: `${e?.needs_followup ?? 0} patients need eligibility follow-up`,
        subtitle: "Coverage verification pending",
        to: "/app/appointments",
      },
      {
        tone: "success",
        icon: Sparkles,
        title: "18 appointment reminders sent",
        subtitle: "Automated by Foresight",
        to: "/app/appointments",
      },
      {
        tone: "pro",
        icon: FileText,
        title: "3 refill requests require review",
        subtitle: "Patient history flagged",
        to: "/app/tasks",
      },
    ];
  }, [data]);

  const risk = data?.claims.risk;
  const recCount = data?.claims.missing_documentation ?? 0;
  const recBody =
    recCount > 0
      ? `${recCount} high-value claims are missing documentation. Review and approve AI recommendations to prevent denial.`
      : `${data?.claims.awaiting_approval ?? 0} claims are awaiting your approval. Review the AI recommendations to keep them moving.`;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-text-primary">
            Welcome back, {orgName ?? "—"}
          </h1>
          <p className="mt-1 text-sm text-text-secondary">
            Here's what's happening across your facility today.
          </p>
        </div>
        <button className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-medium text-text-secondary hover:bg-surface-muted">
          <CalendarDays className="h-4 w-4 text-text-muted" />
          {today}
          <ChevronDown className="h-4 w-4 text-text-muted" />
        </button>
      </div>

      {error && <div className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</div>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((s) => (
          <StatCard key={s.label} stat={s} />
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="self-start lg:col-span-3">
          <div className="flex items-center justify-between px-5 py-4">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-text-primary">AI Insights</h2>
              <span className="flex items-center gap-1.5 rounded-full bg-success-soft px-2 py-0.5 text-xs font-medium text-success">
                <span className="h-1.5 w-1.5 rounded-full bg-success" />
                Live
              </span>
            </div>
            <button
              onClick={() => onNavigate?.("/app/tasks")}
              className="flex items-center gap-1 text-xs font-medium text-accent hover:text-accent-strong"
            >
              View all
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="divide-y divide-border border-t border-border">
            {insights.map((ins, i) => (
              <InsightRow key={i} insight={ins} onNavigate={onNavigate} />
            ))}
          </div>
        </Card>

        <div className="space-y-6 lg:col-span-2">
          <Card className="p-5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">Claims Risk Overview</h2>
              <span className="text-xs text-text-secondary">This week</span>
            </div>
            <div className="my-5">
              <Donut
                segments={[
                  { value: risk?.low ?? 0, color: "rgb(var(--color-success))" },
                  { value: risk?.medium ?? 0, color: "rgb(var(--color-accent))" },
                  { value: risk?.high ?? 0, color: "rgb(var(--color-danger))" },
                ]}
                centerValue={data ? `${data.claims.clean_pct}%` : "—"}
                centerLabel="Clean Claims"
              />
            </div>
            <ul className="space-y-2 text-sm">
              {[
                { color: "bg-success", label: "Low Risk", n: risk?.low },
                { color: "bg-accent", label: "Needs Review", n: risk?.medium },
                { color: "bg-danger", label: "High Risk", n: risk?.high },
              ].map((row) => (
                <li key={row.label} className="flex items-center justify-between">
                  <span className="flex items-center gap-2 text-text-secondary">
                    <span className={`h-2 w-2 rounded-full ${row.color}`} />
                    {row.label}
                  </span>
                  <span className="font-medium tabular-nums text-text-primary">{row.n ?? "—"}</span>
                </li>
              ))}
            </ul>
          </Card>

          <Card className="p-5">
            <div className="flex gap-3">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-warning-soft text-warning">
                <Lightbulb className="h-[18px] w-[18px]" strokeWidth={2} />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-text-primary">Recommended Action</h2>
                <p className="mt-1 text-sm text-text-secondary">{recBody}</p>
              </div>
            </div>
            <button
              onClick={() => onNavigate?.("/app/claims")}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-text-primary px-4 py-2.5 text-sm font-semibold text-surface hover:opacity-90"
            >
              Review Now
              <ArrowRight className="h-4 w-4" />
            </button>
          </Card>
        </div>
      </div>
    </div>
  );
}
