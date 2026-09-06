import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { ArrowRight, CheckCircle2, X } from "lucide-react";

import { publicPost } from "../lib/api";

interface DemoResponse {
  id: string;
  personal_email_flagged: boolean;
  confirmation: string;
  covers: string[];
}

interface DemoModalContextValue {
  open: (source?: string) => void;
}

const DemoModalContext = createContext<DemoModalContextValue | undefined>(undefined);

const ORG_TYPES = [
  { value: "clinic", label: "Clinic" },
  { value: "hospital", label: "Hospital" },
  { value: "medical_group", label: "Medical Group" },
];

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function DemoForm({ source, onClose }: { source: string; onClose: () => void }) {
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [orgType, setOrgType] = useState("clinic");
  const [providers, setProviders] = useState("");
  const [goals, setGoals] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<DemoResponse | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!orgName.trim()) return setError("Organization name is required.");
    if (!EMAIL_RE.test(email)) return setError("Enter a valid work email address.");
    setBusy(true);
    try {
      const res = await publicPost<DemoResponse>("/api/demo-requests", {
        organization_name: orgName.trim(),
        work_email: email.trim(),
        organization_type: orgType,
        provider_count: providers ? Number(providers) : null,
        goals: goals.trim() || null,
        source,
      });
      setDone(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="p-6 sm:p-8">
        <div className="flex items-center gap-3">
          <CheckCircle2 className="h-6 w-6 text-success" />
          <h2 className="text-lg font-semibold text-text-primary">Request received</h2>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-text-secondary">{done.confirmation}</p>
        <div className="mt-4 rounded-lg border border-border bg-surface-muted p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            What the demo covers
          </p>
          <ul className="mt-2 space-y-1.5">
            {done.covers.map((c) => (
              <li key={c} className="flex gap-2 text-sm text-text-secondary">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                {c}
              </li>
            ))}
          </ul>
        </div>
        {done.personal_email_flagged && (
          <p className="mt-3 text-xs text-text-muted">
            You used a personal email address — that's fine, but a work address helps us route your
            request faster.
          </p>
        )}
        <button
          onClick={onClose}
          className="mt-5 w-full rounded-lg bg-text-primary px-4 py-2.5 text-sm font-semibold text-surface hover:opacity-90"
        >
          Done
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="p-6 sm:p-8">
      <h2 className="text-lg font-semibold text-text-primary">Book a demo</h2>
      <p className="mt-1 text-sm text-text-secondary">
        A 30-minute walkthrough of how Foresight fits your clinic or hospital.
      </p>

      <div className="mt-5 space-y-4">
        <Field label="Organization name">
          <input
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            required
            className={inputClass}
            placeholder="Riverside Family Medicine"
          />
        </Field>
        <Field label="Work email">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className={inputClass}
            placeholder="you@yourorg.org"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Organization type">
            <select
              value={orgType}
              onChange={(e) => setOrgType(e.target.value)}
              className={inputClass}
            >
              {ORG_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Number of providers">
            <input
              type="number"
              min={0}
              value={providers}
              onChange={(e) => setProviders(e.target.value)}
              className={inputClass}
              placeholder="e.g. 12"
            />
          </Field>
        </div>
        <Field label="What are you looking to improve?">
          <textarea
            value={goals}
            onChange={(e) => setGoals(e.target.value)}
            rows={3}
            className={inputClass}
            placeholder="Denials, eligibility, front-desk workload…"
          />
        </Field>
      </div>

      {error && (
        <p className="mt-4 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-text-primary px-4 py-2.5 text-sm font-semibold text-surface hover:opacity-90 disabled:opacity-60"
      >
        {busy ? "Sending…" : "Request Demo"}
        <ArrowRight className="h-4 w-4" />
      </button>
    </form>
  );
}

const inputClass =
  "mt-1 block w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm font-medium text-text-secondary">
      {label}
      {children}
    </label>
  );
}

export function DemoModalProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<string | null>(null);

  const open = useCallback((s?: string) => setSource(s ?? "marketing_site"), []);
  const close = useCallback(() => setSource(null), []);

  useEffect(() => {
    if (source === null) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [source, close]);

  const value = useMemo<DemoModalContextValue>(() => ({ open }), [open]);

  return (
    <DemoModalContext.Provider value={value}>
      {children}
      {source !== null && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center"
          onClick={close}
        >
          <div
            className="relative w-full max-w-lg rounded-2xl border border-border bg-surface shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={close}
              aria-label="Close"
              className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-lg text-text-muted hover:bg-surface-muted hover:text-text-primary"
            >
              <X className="h-4 w-4" />
            </button>
            <DemoForm source={source} onClose={close} />
          </div>
        </div>
      )}
    </DemoModalContext.Provider>
  );
}

export function useDemoModal(): DemoModalContextValue {
  const ctx = useContext(DemoModalContext);
  if (!ctx) throw new Error("useDemoModal must be used within <DemoModalProvider>");
  return ctx;
}
