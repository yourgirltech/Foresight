import { useState } from "react";

import { apiFetch } from "../lib/api";
import type { Appeal, AppealDecisionResult, AppealGround, AppealStatus } from "../lib/types";

function money(n: number | string | undefined): string {
  if (n === undefined || n === null) return "—";
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

const STATUS_LABEL: Record<AppealStatus, string> = {
  pending: "Drafting…",
  drafted: "Awaiting your approval",
  insufficient_basis: "No documented basis",
  submission_declined: "Not submitted",
  submitting: "Submitting…",
  submitted: "Submitted — awaiting payer",
  appeal_approved: "Won — claim reversed",
  appeal_partial: "Partly reversed",
  appeal_denied: "Upheld — denial stands",
  error: "Drafting unavailable",
};

const STATUS_TONE: Record<AppealStatus, string> = {
  pending: "bg-slate-100 text-slate-600",
  drafted: "bg-amber-50 text-amber-700",
  insufficient_basis: "bg-violet-50 text-violet-700",
  submission_declined: "bg-slate-100 text-slate-500",
  submitting: "bg-sky-50 text-sky-700",
  submitted: "bg-sky-50 text-sky-700",
  appeal_approved: "bg-emerald-50 text-emerald-700",
  appeal_partial: "bg-amber-50 text-amber-700",
  appeal_denied: "bg-red-50 text-red-700",
  error: "bg-red-50 text-red-700",
};

export function AppealBadge({ status }: { status: AppealStatus | null | undefined }) {
  if (!status) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
        STATUS_TONE[status] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

const GROUND_LABEL: Record<AppealGround["source"], string> = {
  rule_engine_issue: "Rule-engine finding",
  payer_denial_reason: "Payer denial reason",
  action_taken: "Action already taken",
};

function Grounds({ grounds }: { grounds: AppealGround[] }) {
  if (grounds.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Evidence cited</p>
      <ul className="mt-1 space-y-1.5">
        {grounds.map((g, i) => (
          <li key={i} className="text-sm text-slate-600">
            <span className="font-medium text-slate-700">
              {GROUND_LABEL[g.source]}
              {g.ref ? ` — ${g.ref.replace(/_/g, " ")}` : ""}:
            </span>{" "}
            {g.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Shown on the claim detail page for a denied claim (or when an appeal row
 * exists). 11 drafts a letter grounded strictly in the evidence below; a human
 * approves the send. A won appeal is the only thing that reverses the claim.
 */
export function AppealSection({
  claimId,
  claimStatus,
  appeal,
  chain,
  onChanged,
}: {
  claimId: string;
  claimStatus: string;
  appeal: Appeal | null;
  chain: Appeal[];
  onChanged: () => void | Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (claimStatus !== "denied" && !appeal) return null;

  async function call(path: string, body?: unknown) {
    setBusy(path);
    setError(null);
    setNotice(null);
    try {
      const r = await apiFetch<AppealDecisionResult>(path, {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      setNotice(`Commander: ${r.decision.reason_code}`);
      await onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  const outcome = appeal?.resolution_payload?.outcome;
  const resolved = outcome !== undefined;
  const exhausted = outcome === "partial" || outcome === "denied";

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">
          Appeal <span className="font-normal text-slate-400">· 11, grounded strictly in this claim's evidence</span>
        </h2>
        {appeal && <AppealBadge status={appeal.status} />}
      </div>

      {error && <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {notice && (
        <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</p>
      )}

      {/* no appeal yet — offer to start one */}
      {!appeal && claimStatus === "denied" && (
        <div className="mt-3">
          <p className="text-sm text-slate-500">
            This claim was denied. 11 can draft an appeal from the rule-engine findings and the
            recorded denial reason — you review and approve before anything is sent.
          </p>
          <button
            onClick={() => void call(`/api/claims/${claimId}/appeal`)}
            disabled={busy !== null}
            className="mt-3 rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {busy ? "Drafting…" : "Draft an appeal"}
          </button>
        </div>
      )}

      {appeal?.status === "insufficient_basis" && (
        <div className="mt-3">
          <p className="text-sm text-slate-600">
            11 found no documented basis for an automated appeal. This has been flagged for manual
            review — check the denial and appeal by hand if it's worth pursuing.
          </p>
          <Grounds grounds={appeal.grounds} />
        </div>
      )}

      {appeal?.status === "error" && (
        <p className="mt-3 text-sm text-red-700">
          The drafting service was unavailable. This has been flagged for a human — retry, or draft
          the appeal manually.
        </p>
      )}

      {appeal && ["drafted", "submitting", "submitted"].includes(appeal.status) && (
        <div className="mt-3">
          {appeal.letter_text && (
            <div className="rounded-lg border border-slate-100 bg-slate-50/60 p-4">
              <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700">
                {appeal.letter_text}
              </p>
            </div>
          )}
          <Grounds grounds={appeal.grounds} />
          <p className="mt-2 text-xs text-slate-400">
            Draft {appeal.model ? `by ${appeal.model}` : ""} · a person approves before this is sent
          </p>

          {appeal.status === "drafted" && (
            <div className="mt-4 flex gap-3">
              <button
                onClick={() => void call(`/api/appeals/${appeal.id}/approve-submission`)}
                disabled={busy !== null}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {busy?.includes("approve") ? "Submitting…" : "Approve & submit"}
              </button>
              <button
                onClick={() => void call(`/api/appeals/${appeal.id}/decline-submission`)}
                disabled={busy !== null}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
              >
                {busy?.includes("decline") ? "…" : "Don't submit"}
              </button>
            </div>
          )}
        </div>
      )}

      {appeal?.status === "submission_declined" && (
        <p className="mt-3 text-sm text-slate-500">
          You chose not to send this appeal. The claim remains denied.
        </p>
      )}

      {appeal && resolved && (
        <div className="mt-3">
          <p className="text-sm text-slate-700">
            {outcome === "approved" && (
              <>The payer reversed the denial in full. The claim has been marked <strong>paid</strong> ({money(appeal.resolution_payload.reversed_amount)}).</>
            )}
            {outcome === "partial" && (
              <>The payer reversed part of the denial ({money(appeal.resolution_payload.reversed_amount)}). The claim stays denied — decide whether to accept the partial payment, appeal again, or write it off.</>
            )}
            {outcome === "denied" && (
              <>The payer upheld the denial. The automated path is exhausted — a second-level appeal, external review, or a write-off is a human decision now.</>
            )}
          </p>
          <Grounds grounds={appeal.grounds} />
          {exhausted && (
            <button
              onClick={() =>
                void call(`/api/appeals/${appeal.id}/resubmit`, { documentation_affirmed: false })
              }
              disabled={busy !== null}
              className="mt-4 rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
            >
              {busy?.includes("resubmit") ? "Drafting…" : "Appeal again (second level)"}
            </button>
          )}
        </div>
      )}

      {chain.length > 1 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Attempts</p>
          <ol className="mt-1 space-y-1">
            {chain.map((a, i) => (
              <li key={a.id} className="flex items-center gap-2 text-sm">
                <span className="text-xs text-slate-400">#{i + 1}</span>
                <AppealBadge status={a.status} />
                <span className="text-xs text-slate-400">{when(a.created_at)}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
