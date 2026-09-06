import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { PriorAuthBadge, PriorAuthEmergencyPill } from "../components/priorAuth";
import { apiFetch } from "../lib/api";
import type { PriorAuthDecisionResult, PriorAuthDetail } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

function prettyActor(a: string): string {
  if (a.startsWith("human:")) return "Human";
  return a;
}

const HIDE_KEYS = [
  "simulated",
  "basis",
  "generated_at",
  "drafted_at",
  "responded_at",
  "reason",
  "clinical_justification",
  "determination",
  "response_status",
  "authorization_number",
  "patient_name",
];

function PayloadGrid({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(
    ([k, v]) => !HIDE_KEYS.includes(k) && v !== null && v !== "",
  );
  if (entries.length === 0) return null;
  return (
    <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className="text-xs uppercase tracking-wide text-slate-400">{k.replace(/_/g, " ")}</dt>
          <dd className="text-slate-700">{typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PriorAuthDetailPage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<PriorAuthDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<PriorAuthDetail>(`/api/prior-authorizations/${id}`));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(path: "approve-submission" | "decline-submission" | "resubmit") {
    setBusy(path);
    setNotice(null);
    setError(null);
    try {
      const r = await apiFetch<PriorAuthDecisionResult>(
        `/api/prior-authorizations/${id}/${path}`,
        { method: "POST", body: path === "resubmit" ? JSON.stringify({}) : undefined },
      );
      setNotice(
        `Commander: ${r.decision.reason_code}` +
          (r.decision.route_to ? ` → ${r.decision.route_to}` : ""),
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error && !data) {
    return (
      <div className="space-y-4">
        <Link to="/app/prior-auth" className="text-sm text-brand-600 hover:underline">
          ← Prior authorization
        </Link>
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      </div>
    );
  }
  if (!data) return <p className="text-slate-400">Loading…</p>;

  const { prior_authorization: pa, chain, activity_log, appointment } = data;
  const det = pa.determination_payload ?? {};
  const req = pa.request_payload ?? {};
  const resp = pa.response_payload ?? {};
  const canApprove = pa.status === "required_draft";
  const canResubmit = pa.status === "auth_denied" || pa.status === "info_needed";

  return (
    <div className="space-y-6">
      <Link to="/app/prior-auth" className="text-sm text-brand-600 hover:underline">
        ← Prior authorization
      </Link>

      <header className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">
              {pa.procedure_code || "No procedure code"}
              {pa.procedure_description && (
                <span className="ml-2 text-sm font-normal text-slate-500">
                  {pa.procedure_description}
                </span>
              )}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {pa.patient_name} · member {pa.patient_member_id || "—"} · {pa.payer_name ?? "—"} ·{" "}
              {pa.place_of_service}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <PriorAuthBadge status={pa.status} />
            {pa.is_emergency && <PriorAuthEmergencyPill />}
          </div>
        </div>
        {pa.authorization_number && (
          <p className="mt-3 text-sm text-emerald-700">
            Authorization number: <strong className="tabular-nums">{pa.authorization_number}</strong>
          </p>
        )}
        {appointment && (
          <p className="mt-2 text-xs text-slate-400">
            Linked appointment:{" "}
            <Link
              to={`/app/appointments/${appointment.id}`}
              className="text-brand-600 hover:underline"
            >
              {when(appointment.scheduled_at)}
            </Link>{" "}
            — the appointment carries no "blocked" state; prior-auth status only informs staff.
          </p>
        )}
      </header>

      {notice && (
        <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</p>
      )}
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {pa.is_emergency && (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
          <h2 className="text-sm font-semibold text-emerald-900">
            Not required — emergency exemption
          </h2>
          <p className="mt-2 text-sm text-emerald-800">
            This is an emergency / urgent service. Prior authorization does not apply and was never a
            precondition for care. The determination was run and recorded for billing; no request was
            drafted, no approval was asked for, and nothing was escalated.
          </p>
        </section>
      )}

      {/* --- Determination (02) --- */}
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">
          Determination <span className="font-normal text-slate-400">· 02, deterministic</span>
        </h2>
        {typeof det.reason === "string" && (
          <p className="mt-2 text-sm text-slate-700">{det.reason}</p>
        )}
        <PayloadGrid payload={det} />
        {det.recheck_recommended === true && (
          <p className="mt-3 text-sm text-violet-700">
            Add the missing detail (procedure code) and re-run to get a determination.
          </p>
        )}
      </section>

      {/* --- Drafted request packet --- */}
      {Object.keys(req).length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">
            Drafted request{" "}
            <span className="font-normal text-slate-400">
              · {String(req.channel ?? "electronic")} · reviewed by a human before it is sent
            </span>
          </h2>
          {typeof req.clinical_justification === "string" && (
            <p className="mt-2 rounded-lg border border-slate-100 bg-slate-50/60 p-3 text-sm text-slate-700">
              {req.clinical_justification}
            </p>
          )}
          <PayloadGrid payload={req} />

          {canApprove && (
            <div className="mt-4 flex gap-3">
              <button
                onClick={() => void act("approve-submission")}
                disabled={busy !== null}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {busy === "approve-submission" ? "Submitting…" : "Approve & submit"}
              </button>
              <button
                onClick={() => void act("decline-submission")}
                disabled={busy !== null}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
              >
                {busy === "decline-submission" ? "Declining…" : "Don't submit"}
              </button>
            </div>
          )}
        </section>
      )}

      {/* --- Payer response --- */}
      {Object.keys(resp).length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">
            Payer response <span className="font-normal text-slate-400">· simulated</span>
          </h2>
          {typeof resp.reason === "string" && (
            <p className="mt-2 text-sm text-slate-700">{resp.reason}</p>
          )}
          <PayloadGrid payload={resp} />
          {canResubmit && (
            <button
              onClick={() => void act("resubmit")}
              disabled={busy !== null}
              className="mt-4 rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
            >
              {busy === "resubmit"
                ? "Resubmitting…"
                : "Attach clinicals & resubmit (new attempt)"}
            </button>
          )}
        </section>
      )}

      {/* --- Resubmit chain --- */}
      {chain.length > 1 && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">Attempts</h2>
          <ol className="mt-3 space-y-2">
            {chain.map((c, i) => (
              <li key={c.id} className="flex items-center gap-3 text-sm">
                <span className="text-xs font-medium text-slate-400">#{i + 1}</span>
                {c.id === pa.id ? (
                  <span className="font-medium text-slate-800">this attempt</span>
                ) : (
                  <Link to={`/app/prior-auth/${c.id}`} className="text-brand-600 hover:underline">
                    {c.procedure_code || "no code"}
                  </Link>
                )}
                <PriorAuthBadge status={c.status} />
                <span className="text-xs text-slate-400">{when(c.created_at)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* --- Activity --- */}
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">Activity</h2>
        <ol className="mt-3 space-y-2">
          {activity_log.map((a, n) => (
            <li key={n} className="flex gap-3 text-sm">
              <span className="w-40 shrink-0 text-xs text-slate-400">{when(a.created_at)}</span>
              <span className="w-40 shrink-0 font-medium text-slate-600">{prettyActor(a.actor)}</span>
              <span className="text-slate-700">{a.action}</span>
            </li>
          ))}
          {activity_log.length === 0 && <li className="text-sm text-slate-400">No activity yet.</li>}
        </ol>
      </section>
    </div>
  );
}
