import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  CARD_FIELDS,
  CardScanBadge,
  ConfidenceHint,
  FIELD_LABEL,
  fieldNeedsAttention,
} from "../components/cardScan";
import { apiFetch } from "../lib/api";
import type { CardField, CardScanConfirmResult, CardScanDetail } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

type Draft = Record<CardField, string>;

export function CardScanReviewPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<CardScanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await apiFetch<CardScanDetail>(`/api/card-scans/${id}`);
      setData(d);
      setError(null);
      const f = d.card_scan.extracted_fields;
      const confirmed = (f as { confirmed?: Partial<Record<CardField, string | null>> }).confirmed;
      const src = confirmed ?? f;
      setDraft({
        member_id: src.member_id ?? "",
        group_number: src.group_number ?? "",
        payer_name: src.payer_name ?? "",
        plan_type: src.plan_type ?? "",
      });
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const scan = data?.card_scan;
  const terminal = scan?.status === "confirmed" || scan?.status === "rejected";

  // plan_type may legitimately be blank (not on the card); the other three must
  // have a value before confirm.
  const canConfirm = useMemo(() => {
    if (!draft) return false;
    return (["member_id", "group_number", "payer_name"] as CardField[]).every(
      (f) => draft[f].trim().length > 0,
    );
  }, [draft]);

  async function confirm() {
    if (!draft || !scan) return;
    setBusy("confirm");
    setError(null);
    try {
      const r = await apiFetch<CardScanConfirmResult>(`/api/card-scans/${id}/confirm`, {
        method: "POST",
        body: JSON.stringify({
          member_id: draft.member_id.trim() || null,
          group_number: draft.group_number.trim() || null,
          payer_name: draft.payer_name.trim() || null,
          plan_type: draft.plan_type.trim() || null,
          appointment_id: scan.appointment_id,
        }),
      });
      setNotice(
        r.applied_to_appointment
          ? `Applied. The linked appointment's member ID${
              r.matched_payer_id ? " and payer" : ""
            } were updated.`
          : "Confirmed. No appointment was linked, so nothing was written back.",
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    setError(null);
    try {
      await apiFetch(`/api/card-scans/${id}/reject`, { method: "POST" });
      navigate("/app/front-desk");
    } catch (e) {
      setError(String(e));
      setBusy(null);
    }
  }

  if (error && !data) {
    return (
      <div className="space-y-4">
        <Link to="/app/front-desk" className="text-sm text-brand-600 hover:underline">
          ← Front desk
        </Link>
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      </div>
    );
  }
  if (!data || !scan || !draft) return <p className="text-slate-400">Loading…</p>;

  const conf = scan.field_confidence;

  return (
    <div className="space-y-6">
      <Link to="/app/front-desk" className="text-sm text-brand-600 hover:underline">
        ← Front desk
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">
            {scan.patient_name || "Card scan"}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Scanned {when(scan.created_at)}
            {scan.model ? ` · read by ${scan.model}` : ""}
          </p>
        </div>
        <CardScanBadge status={scan.status} />
      </header>

      {notice && (
        <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{notice}</p>
      )}
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {scan.status === "error" && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          The card couldn't be read: {scan.extracted_fields.error ?? "vision service unavailable"}.
          Re-take the photo and upload again.
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* --- the image --- */}
        <section className="rounded-xl border border-slate-200 bg-white p-3">
          {data.image_url ? (
            <img
              src={data.image_url}
              alt="Insurance card"
              className="max-h-[360px] w-full rounded-lg object-contain"
            />
          ) : (
            <div className="grid h-48 place-items-center px-4 text-center text-sm text-slate-400">
              {scan.image_purged_at
                ? `Image deleted ${when(scan.image_purged_at)} per the retention policy. The extracted fields are kept.`
                : "image not available"}
            </div>
          )}
        </section>

        {/* --- editable fields --- */}
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">
            Coverage fields{" "}
            <span className="font-normal text-slate-400">· read from the card — correct anything wrong</span>
          </h2>
          <div className="mt-4 space-y-4">
            {CARD_FIELDS.map((f) => {
              const meta = conf[f];
              const attention = !terminal && fieldNeedsAttention(meta);
              return (
                <div key={f}>
                  <div className="flex items-baseline justify-between">
                    <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
                      {FIELD_LABEL[f]}
                      {f === "plan_type" && (
                        <span className="ml-1 lowercase text-slate-400">(optional)</span>
                      )}
                    </label>
                    <ConfidenceHint
                      confidence={meta?.confidence}
                      legible={meta?.legible}
                      absent={meta?.absent}
                    />
                  </div>
                  <input
                    value={draft[f]}
                    disabled={terminal}
                    onChange={(e) => setDraft({ ...draft, [f]: e.target.value })}
                    className={[
                      "mt-1 w-full rounded-md border px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-1",
                      attention
                        ? "border-amber-300 bg-amber-50/40 focus:border-amber-500 focus:ring-amber-500"
                        : "border-slate-300 focus:border-brand-500 focus:ring-brand-500",
                      terminal ? "bg-slate-50 text-slate-500" : "",
                    ].join(" ")}
                  />
                </div>
              );
            })}
          </div>

          {data.appointment ? (
            <p className="mt-4 text-xs text-slate-400">
              On confirm, the member ID (and payer, if it matches your directory) are written to the
              linked appointment for{" "}
              <Link
                to={`/app/appointments/${data.appointment.id}`}
                className="text-brand-600 hover:underline"
              >
                {data.appointment.patient_name}
              </Link>
              .
            </p>
          ) : (
            <p className="mt-4 text-xs text-slate-400">
              No appointment is linked — confirming records the values on the scan but writes nothing
              back.
            </p>
          )}

          {!terminal && (
            <div className="mt-4 flex gap-3">
              <button
                onClick={() => void confirm()}
                disabled={!canConfirm || busy !== null}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {busy === "confirm" ? "Applying…" : "Confirm & apply"}
              </button>
              <button
                onClick={() => void reject()}
                disabled={busy !== null}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-60"
              >
                {busy === "reject" ? "Discarding…" : "Discard"}
              </button>
            </div>
          )}
          {!terminal && !canConfirm && (
            <p className="mt-2 text-xs text-amber-600">
              Fill member ID, group number, and payer before confirming.
            </p>
          )}
          {scan.status === "confirmed" && (
            <p className="mt-4 text-xs text-slate-400">
              Confirmed by a reviewer {when(scan.reviewed_at)}.
              {scan.image_retain_until &&
                ` The image is retained until ${when(scan.image_retain_until)}, then deleted.`}
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
