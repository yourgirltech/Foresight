import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Camera, Upload } from "lucide-react";

import { useAuth } from "../auth/useAuth";
import { CameraCapture } from "../components/CameraCapture";
import { CardScanBadge } from "../components/cardScan";
import { apiFetch, apiUpload } from "../lib/api";
import type { CardScan, CardScanListResponse } from "../lib/types";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

const OPEN: CardScan["status"][] = ["pending", "extracted", "needs_review"];

function Row({ scan }: { scan: CardScan }) {
  const payer = scan.extracted_fields.payer_name;
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-3 text-slate-600">
        <Link to={`/app/front-desk/${scan.id}`} className="font-medium text-brand-700 hover:underline">
          {scan.patient_name || "Card scan"}
        </Link>
      </td>
      <td className="px-4 py-3 text-slate-600">
        {payer || <span className="text-slate-400">—</span>}
      </td>
      <td className="px-4 py-3 tabular-nums text-slate-500">
        {scan.extracted_fields.member_id || <span className="text-slate-400">—</span>}
      </td>
      <td className="px-4 py-3 text-slate-500">{when(scan.created_at)}</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <CardScanBadge status={scan.status} />
          {scan.applied_to_appointment && (
            <span className="text-xs text-emerald-600">→ appointment updated</span>
          )}
        </div>
      </td>
    </tr>
  );
}

function Table({ rows, empty }: { rows: CardScan[]; empty: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-3">Patient</th>
            <th className="px-4 py-3">Payer (read)</th>
            <th className="px-4 py-3">Member ID (read)</th>
            <th className="px-4 py-3">Scanned</th>
            <th className="px-4 py-3">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="px-4 py-6 text-center text-slate-400">
                {empty}
              </td>
            </tr>
          )}
          {rows.map((s) => (
            <Row key={s.id} scan={s} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FrontDeskPage() {
  const { organization } = useAuth();
  const [data, setData] = useState<CardScanListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      setData(await apiFetch<CardScanListResponse>("/api/card-scans"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function upload(file: File) {
    setCameraOpen(false);
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("image", file);
      const { card_scan } = await apiUpload<{ card_scan: CardScan }>("/api/card-scans", form);
      // jump straight to review for the just-uploaded scan
      navigate(`/app/front-desk/${card_scan.id}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const all = data?.card_scans ?? [];
  const open = all.filter((s) => OPEN.includes(s.status));
  const done = all.filter((s) => !OPEN.includes(s.status));

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Front desk</h1>
        <p className="mt-1 text-sm text-slate-500">
          {organization?.name} · photograph a patient's insurance card and 03 reads the coverage
          fields off it. Every value is a <span className="font-medium text-slate-600">draft</span> —
          a person confirms or corrects it before anything is saved to an appointment.
        </p>
      </header>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {/* --- Capture — camera first (front desk is usually a tablet) --- */}
      <section className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center">
        <button
          onClick={() => setCameraOpen(true)}
          disabled={busy}
          className="inline-flex items-center gap-2 rounded-md bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          <Camera className="h-4 w-4" />
          {busy ? "Reading the card…" : "Take a photo of the card"}
        </button>
        <p className="mt-3 text-xs text-slate-400">
          Opens your camera.{" "}
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            className="inline-flex items-center gap-1 font-medium text-brand-600 hover:underline disabled:opacity-60"
          >
            <Upload className="h-3 w-3" />
            Upload a saved image
          </button>{" "}
          instead.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          JPEG / PNG / WebP / HEIC, up to 10 MB. The image is stored privately and is only used to
          extract the fields below.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/heic"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
            e.target.value = "";
          }}
        />
      </section>

      {cameraOpen && (
        <CameraCapture
          onCapture={(file) => void upload(file)}
          onClose={() => setCameraOpen(false)}
          onPickFile={() => {
            setCameraOpen(false);
            fileRef.current?.click();
          }}
        />
      )}

      {data === null && !error && <p className="text-slate-400">Loading…</p>}

      {data && (
        <>
          <section className="overflow-hidden rounded-xl border border-amber-200 bg-white">
            <div className="border-b border-amber-100 bg-amber-50/50 px-4 py-3">
              <h2 className="text-sm font-semibold text-amber-900">
                To review
                {open.length > 0 && (
                  <span className="ml-2 rounded-full bg-amber-200 px-1.5 py-0.5 text-xs text-amber-800">
                    {open.length}
                  </span>
                )}
              </h2>
            </div>
            <Table rows={open} empty="Nothing waiting. Upload a card photo above." />
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-3">
              <h2 className="text-sm font-semibold text-slate-700">Resolved</h2>
            </div>
            <Table rows={done} empty="No confirmed or discarded scans yet." />
          </section>

          <p className="text-xs text-slate-400">
            No extracted value is written to an appointment until someone opens the scan and presses{" "}
            <span className="font-medium text-slate-500">Confirm &amp; apply</span>. Run{" "}
            <code>scripts/seed_card_scans.py</code> for demo data.
          </p>
        </>
      )}
    </div>
  );
}
