import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CheckHistory, EmergencyPill } from "../components/eligibility";
import { prettyActor } from "../components/claims";
import { apiFetch } from "../lib/api";
import type { EligibilityCheckDetail, EligibilityDecisionResult } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

export function EligibilityCheckDetailPage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<EligibilityCheckDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memberId, setMemberId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<EligibilityCheckDetail>(`/api/eligibility-checks/${id}`));
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

  const { check, chain, activity_log } = data;
  const latest = chain[chain.length - 1] ?? check;

  async function recheck() {
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

      <header className="rounded-xl border border-rose-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{check.patient_name}</h1>
            <p className="mt-1 text-sm text-slate-500">
              Emergency / unscheduled registration · registered {when(check.created_at)} ·{" "}
              {check.payer_name ?? "no payer on file"}
            </p>
          </div>
          <EmergencyPill />
        </div>
        <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">
          This verification runs in parallel with care. Its result — including &ldquo;needs more
          info&rdquo; or &ldquo;check failed&rdquo; — never gated, delayed, or was a precondition for
          anything. It is recorded for staff and billing, and is re-checked when more patient
          information exists.
        </p>
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
          <CheckHistory checks={chain} />
        </div>

        <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-slate-100 pt-4">
          <label className="text-xs text-slate-500">
            Member ID now on file
            <input
              value={memberId}
              onChange={(e) => setMemberId(e.target.value)}
              placeholder="e.g. BX412900037"
              className="mt-1 block rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <button
            onClick={() => void recheck()}
            disabled={busy}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
          >
            {busy ? "Re-checking…" : "Re-check with new info"}
          </button>
        </div>
      </section>

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
