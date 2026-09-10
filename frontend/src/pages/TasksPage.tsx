import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { apiFetch } from "../lib/api";
import type { TaskItem, TasksResponse } from "../lib/types";

const KIND_LABEL: Record<TaskItem["kind"], string> = {
  escalation: "Escalation",
  approval: "Needs approval",
  prior_auth: "Prior auth",
  appeal: "Appeal",
  reminder: "Reminder call",
};
const KIND_TONE: Record<TaskItem["kind"], string> = {
  escalation: "bg-red-50 text-red-700",
  approval: "bg-amber-50 text-amber-700",
  prior_auth: "bg-violet-50 text-violet-700",
  appeal: "bg-sky-50 text-sky-700",
  reminder: "bg-slate-100 text-slate-600",
};

function when(ts: string): string {
  return new Date(ts).toLocaleString();
}

export function TasksPage() {
  const { organization } = useAuth();
  const [data, setData] = useState<TasksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<TaskItem["kind"] | "all">("all");

  useEffect(() => {
    apiFetch<TasksResponse>("/api/tasks").then(setData).catch((e) => setError(String(e)));
  }, []);

  const all = data?.tasks ?? [];
  const shown = filter === "all" ? all : all.filter((t) => t.kind === filter);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Tasks</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · everything the AI has surfaced for a human — escalations, approvals
          to make, prior auths and appeals to review, reminder calls that need a follow-up.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {data === null && !error && <p className="text-slate-400">Loading…</p>}

      {data && (
        <>
          <div className="flex flex-wrap gap-2">
            {(["all", "escalation", "approval", "prior_auth", "appeal", "reminder"] as const).map((k) => {
              const count = k === "all" ? all.length : data.counts[k] ?? 0;
              return (
                <button
                  key={k}
                  onClick={() => setFilter(k)}
                  className={`rounded-full px-3 py-1 text-xs font-medium ${
                    filter === k
                      ? "bg-brand-600 text-white"
                      : "border border-slate-300 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {k === "all" ? "All" : KIND_LABEL[k]} · {count}
                </button>
              );
            })}
          </div>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <ul className="divide-y divide-slate-100">
              {shown.length === 0 && (
                <li className="px-4 py-8 text-center text-slate-400">
                  Nothing here — the AI has no open items{filter === "all" ? "" : ` of this kind`}.
                </li>
              )}
              {shown.map((t) => {
                const body = (
                  <div className="flex items-start gap-3 px-4 py-3">
                    <span
                      className={`mt-0.5 inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ${KIND_TONE[t.kind]}`}
                    >
                      {KIND_LABEL[t.kind]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-800">{t.title}</p>
                      <p className="text-xs text-slate-500">{t.detail}</p>
                    </div>
                    <span className="shrink-0 text-xs text-slate-400">{when(t.created_at)}</span>
                  </div>
                );
                return (
                  <li key={t.id} className="hover:bg-slate-50">
                    {t.link ? <Link to={t.link}>{body}</Link> : body}
                  </li>
                );
              })}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
