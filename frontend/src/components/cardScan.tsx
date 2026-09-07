import type { CardField, CardScanStatus, FieldConfidence } from "../lib/types";

const STATUS_LABEL: Record<CardScanStatus, string> = {
  pending: "Reading…",
  extracted: "Ready to confirm",
  needs_review: "Needs your review",
  confirmed: "Confirmed",
  rejected: "Discarded",
  error: "Couldn't read",
};

// extracted still needs a human confirm — it only means the UI can pre-fill.
const STATUS_TONE: Record<CardScanStatus, string> = {
  pending: "bg-slate-100 text-slate-600",
  extracted: "bg-sky-50 text-sky-700",
  needs_review: "bg-amber-50 text-amber-700",
  confirmed: "bg-emerald-50 text-emerald-700",
  rejected: "bg-slate-100 text-slate-500",
  error: "bg-red-50 text-red-700",
};

export function cardScanLabel(s: CardScanStatus): string {
  return STATUS_LABEL[s] ?? s;
}

export function CardScanBadge({ status }: { status: CardScanStatus | null | undefined }) {
  if (!status) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
        STATUS_TONE[status] ?? "bg-slate-100 text-slate-600"
      }`}
    >
      {cardScanLabel(status)}
    </span>
  );
}

export const FIELD_LABEL: Record<CardField, string> = {
  member_id: "Member ID",
  group_number: "Group number",
  payer_name: "Payer / insurer",
  plan_type: "Plan type",
};

export const CARD_FIELDS: CardField[] = ["member_id", "group_number", "payer_name", "plan_type"];

const CONF_TONE: Record<FieldConfidence, string> = {
  high: "text-emerald-600",
  medium: "text-amber-600",
  low: "text-red-600",
};

export function ConfidenceHint({
  confidence,
  legible,
  absent,
}: {
  confidence?: FieldConfidence;
  legible?: boolean;
  absent?: boolean;
}) {
  if (absent) return <span className="text-xs text-slate-400">not printed on the card</span>;
  if (legible === false || !confidence) {
    return <span className="text-xs font-medium text-red-600">not legible — type it from the card</span>;
  }
  return (
    <span className={`text-xs font-medium ${CONF_TONE[confidence]}`}>
      {confidence} confidence
    </span>
  );
}

/** A field the model could not read cleanly — amber, per spec §7.2. */
export function fieldNeedsAttention(meta?: { legible?: boolean; confidence?: FieldConfidence }): boolean {
  if (!meta) return true;
  return meta.legible === false || meta.confidence === "low";
}
