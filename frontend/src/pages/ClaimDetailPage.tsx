import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { AppealSection } from "../components/appeals";
import {
  ApprovalBadge,
  RiskBadge,
  SeverityBadge,
  StatusBadge,
  prettyActor,
  prettyIssueType,
} from "../components/claims";
import { apiFetch } from "../lib/api";
import type { ClaimDetail, DecisionResult, RecommendationAction } from "../lib/types";

const ACTION_LABEL: Record<RecommendationAction, string> = {
  submit_authorization_request: "Submit prior-authorization request",
  request_documentation: "Request missing documentation",
  payer_status_follow_up: "Open payer status follow-up",
  resubmit_corrected_coding: "Resubmit claim with corrected coding",
};

const MANUAL_ACTIONS = new Set<RecommendationAction>(["resubmit_corrected_coding"]);

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

export function ClaimDetailPage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<ClaimDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<ClaimDetail>(`/api/claims/${id}`));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(kind: "approve" | "decline" | "reanalyze") {
    setBusy(kind);
    setNotice(null);
    setError(null);
    try {
      const r = await apiFetch<DecisionResult>(`/api/claims/${id}/${kind}`, { method: "POST" });
      setNotice(
        `${kind === "reanalyze" ? "Re-analyzed" : kind === "approve" ? "Approved" : "Declined"} — ` +
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
        <Link to="/app/claims" className="text-sm text-brand-600 hover:underline">
          ← Claims
        </Link>
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      </div>
    );
  }
  if (!data) return <p className="text-slate-400">Loading…</p>;

  const { claim, payer, issues, recommendation, activity_log, escalations, follow_ups, appeals, appeal } =
    data;
  const rec = recommendation;
  const canDecide = claim.status === "awaiting_approval" && rec?.approval_status === "pending";
  const isManualRec = rec ? MANUAL_ACTIONS.has(rec.action_type) : false;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Link to="/app/claims" className="text-sm text-brand-600 hover:underline">
          ← Claims
        </Link>
        <button
          onClick={() => void act("reanalyze")}
          disabled={busy !== null}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60"
        >
          {busy === "reanalyze" ? "Re-analyzing…" : "Re-analyze"}
        </button>
      </div>

      <header className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{claim.claim_id}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {claim.patient_name} · member {claim.patient_member_id} · {payer?.name ?? "—"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <RiskBadge level={claim.risk_level} score={claim.risk_score} />
            <StatusBadge status={claim.status} />
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs uppercase text-slate-400">Billed amount</dt>
            <dd className="tabular-nums text-slate-700">${claim.amount}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-400">Authorization</dt>
            <dd className={claim.authorization_present ? "text-slate-700" : "text-red-600"}>
              {claim.authorization_present ? "present" : "missing"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-400">Documentation</dt>
            <dd className={claim.documentation_present ? "text-slate-700" : "text-red-600"}>
              {claim.documentation_present ? "present" : "missing"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase text-slate-400">Coding</dt>
            <dd className={claim.coding_matches ? "text-slate-700" : "text-red-600"}>
              {claim.coding_matches ? "matches record" : "mismatch"}
            </dd>
          </div>
        </dl>
      </header>

      {notice && (
        <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</p>
      )}
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {/* --- Deterministic findings (06) --- */}
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">
          Deterministic findings <span className="font-normal text-slate-400">· rule engine (06)</span>
        </h2>
        {issues.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No rule-engine findings on this claim.</p>
        ) : (
          <ul className="mt-3 space-y-3">
            {issues.map((i, n) => (
              <li key={n} className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-800">
                    {prettyIssueType(i.issue_type)}
                  </span>
                  <SeverityBadge severity={i.severity} />
                </div>
                <p className="mt-1 text-sm text-slate-600">{i.description}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* --- Reasoning (07) --- */}
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">
          Reasoning <span className="font-normal text-slate-400">· 07, grounded in the findings above</span>
        </h2>
        {claim.reasoning_summary ? (
          <>
            <p className="mt-3 text-sm text-slate-700">{claim.reasoning_summary}</p>
            {claim.reasoning_detail && claim.reasoning_detail.length > 0 && (
              <ul className="mt-3 space-y-2">
                {claim.reasoning_detail.map((d, n) => (
                  <li key={n} className="text-sm text-slate-600">
                    <span className="font-medium text-slate-700">{prettyIssueType(d.issue_type)}:</span>{" "}
                    {d.explanation}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-xs text-slate-400">Generated {when(claim.reasoning_generated_at)}</p>
          </>
        ) : (
          <p className="mt-3 text-sm text-slate-500">Not generated yet.</p>
        )}
      </section>

      {/* --- Recommendation (08) + human decision --- */}
      {rec && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">
              Recommendation <span className="font-normal text-slate-400">· 08</span>
            </h2>
            <ApprovalBadge status={rec.approval_status} />
          </div>
          <p className="mt-3 text-base font-medium text-slate-900">
            {ACTION_LABEL[rec.action_type] ?? rec.action_type}
          </p>
          <p className="mt-1 text-sm text-slate-600">{rec.rationale}</p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
            <span>
              Confidence: <strong>{rec.confidence}</strong>
            </span>
            {rec.low_confidence && (
              <span className="rounded bg-red-50 px-1.5 py-0.5 font-medium text-red-700">
                low confidence — routed to escalation, not one-click approval
              </span>
            )}
            {isManualRec && (
              <span className="rounded bg-violet-50 px-1.5 py-0.5 font-medium text-violet-700">
                manual action — a person performs this after approval, no agent executes it
              </span>
            )}
          </div>

          {canDecide && (
            <div className="mt-4 flex gap-3">
              <button
                onClick={() => void act("approve")}
                disabled={busy !== null}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {busy === "approve" ? "Approving…" : "Approve"}
              </button>
              <button
                onClick={() => void act("decline")}
                disabled={busy !== null}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
              >
                {busy === "decline" ? "Declining…" : "Decline"}
              </button>
            </div>
          )}
          {rec.approval_status !== "pending" && (
            <p className="mt-3 text-xs text-slate-400">
              {rec.approval_status} {when(rec.decided_at)}
            </p>
          )}
        </section>
      )}

      {/* --- Appeal (11) — for a denied claim --- */}
      <AppealSection
        claimId={claim.id}
        claimStatus={claim.status}
        appeal={appeal}
        chain={appeals}
        onChanged={load}
      />

      {/* --- Manual action required banner --- */}
      {claim.status === "manual_action_required" && (
        <section className="rounded-xl border border-violet-200 bg-violet-50 p-5">
          <h2 className="text-sm font-semibold text-violet-900">Approved — manual action required</h2>
          <p className="mt-2 text-sm text-violet-800">
            A human approved this recommendation. The action itself (resubmitting the claim to the
            payer) is performed by clinic staff — no agent carries it out. This task has been logged
            and is waiting on a person.
          </p>
        </section>
      )}

      {/* --- Escalations --- */}
      {escalations.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">
            Escalations <span className="font-normal text-slate-400">· 12</span>
          </h2>
          <ul className="mt-3 space-y-2">
            {escalations.map((e, n) => (
              <li key={n} className="text-sm">
                <span className="font-medium text-slate-800">{e.reason_code}</span>
                <span className="text-slate-400"> · {e.originating_agent} · {when(e.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* --- Executed records --- */}
      {follow_ups.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">Executed actions</h2>
          <ul className="mt-3 space-y-2">
            {follow_ups.map((f, n) => (
              <li key={n} className="text-sm text-slate-600">
                <span className="font-medium text-slate-800">
                  {f.kind === "payer_reminder" ? "Payer reminder" : "Follow-up"}
                </span>{" "}
                — {f.note}
                <span className="text-slate-400">
                  {" "}
                  · due {when(f.due_at)} · {f.simulated_send ? "simulated send" : "sent"}{" "}
                  {when(f.sent_at)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* --- Activity timeline --- */}
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
