import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Check, ShieldCheck } from "lucide-react";

/**
 * Split-screen auth shell. Left: a fixed-identity brand panel (deep blue
 * gradient, value prop, trust line) — hidden below lg. Right: the form.
 *
 * Type: the whole shell is set in Hanken Grotesk (`font-grotesk`), scoped
 * here — the rest of the app keeps the default sans stack. Motion: content
 * rises in with a small stagger; the brand-panel glow drifts slowly. Both
 * are disabled under prefers-reduced-motion (see index.css).
 */

const BRAND_BG =
  "radial-gradient(120% 120% at 0% 0%, rgba(59,130,246,0.55), transparent 55%)," +
  "radial-gradient(90% 90% at 100% 100%, rgba(37,99,235,0.45), transparent 60%)," +
  "linear-gradient(155deg, #0b1f4d 0%, #14337a 55%, #1d4ed8 100%)";

// The full Foresight scope — patient access + the revenue cycle — not just claims.
const PROOF = [
  "Front desk on autopilot — scheduling, reminders & intake",
  "Insurance eligibility verified before the visit",
  "Claim denial risk caught before submission",
  "Every consequential action stays human-approved",
];

export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen bg-background font-grotesk">
      {/* -------- brand panel -------- */}
      <aside
        className="relative hidden w-[44%] shrink-0 flex-col justify-between overflow-hidden p-14 xl:w-[42%] xl:p-16 lg:flex"
        style={{ background: BRAND_BG }}
      >
        <div className="fp-dotgrid pointer-events-none absolute inset-0 opacity-[0.12]" />
        <div className="auth-drift pointer-events-none absolute -right-28 top-1/4 h-80 w-80 rounded-full bg-white/10 blur-3xl" />
        <div className="auth-drift-slow pointer-events-none absolute -left-24 bottom-0 h-72 w-72 rounded-full bg-sky-400/10 blur-3xl" />

        <Link
          to="/"
          className="auth-rise auth-rise-1 relative flex items-center gap-2.5"
        >
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-white/15 text-white ring-1 ring-inset ring-white/25">
            <ShieldCheck className="h-5 w-5" strokeWidth={2.25} />
          </span>
          <span className="text-lg font-semibold tracking-tight text-white">Foresight</span>
        </Link>

        <div className="relative">
          <h2 className="auth-rise auth-rise-2 font-display text-[2rem] font-semibold leading-[1.18] text-white xl:text-[2.3rem]">
            Patient access and the revenue cycle — with a human still in charge.
          </h2>
          <p className="auth-rise auth-rise-3 mt-5 max-w-sm text-[15px] leading-[1.75] text-white/70">
            Foresight&apos;s agents run the repetitive work across the front desk, eligibility,
            and billing. Your team approves anything that matters.
          </p>
          <ul className="auth-rise auth-rise-4 mt-11 space-y-5">
            {PROOF.map((p) => (
              <li key={p} className="flex items-start gap-3.5 text-[15px] leading-snug text-white/80">
                <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/15 text-white ring-1 ring-inset ring-white/20">
                  <Check className="h-3 w-3" strokeWidth={3} />
                </span>
                {p}
              </li>
            ))}
          </ul>
        </div>

        <p className="auth-rise auth-rise-5 relative text-[13px] font-medium tracking-wide text-white/55">
          Multi-tenant · Human-in-the-loop · Audit-ready
        </p>
      </aside>

      {/* -------- form panel -------- */}
      <main className="flex flex-1 flex-col">
        <div className="flex items-center justify-between px-6 pt-8 sm:px-12 sm:pt-10">
          <Link
            to="/"
            className="flex items-center gap-2 text-sm font-medium text-text-primary lg:hidden"
          >
            <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-white">
              <ShieldCheck className="h-4 w-4" strokeWidth={2.25} />
            </span>
            Foresight
          </Link>
          <Link
            to="/"
            className="ml-auto inline-flex items-center gap-1.5 text-sm font-medium text-text-secondary transition-colors hover:text-text-primary"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to site
          </Link>
        </div>

        <div className="flex flex-1 items-center justify-center px-6 py-14 sm:px-12 lg:py-20">
          <div className="w-full max-w-[25rem]">
            <span className="auth-rise auth-rise-1 mb-5 flex h-1 w-10 rounded-full bg-accent/70" />
            <h1 className="auth-rise auth-rise-2 font-display text-[2.1rem] font-semibold leading-[1.15] tracking-tight text-text-primary">
              {title}
            </h1>
            {subtitle && (
              <p className="auth-rise auth-rise-3 mt-3.5 text-[15px] leading-[1.7] text-text-secondary">
                {subtitle}
              </p>
            )}
            <div className="auth-rise auth-rise-4 mt-9">{children}</div>
            {footer && (
              <div className="auth-rise auth-rise-5 mt-8 text-sm text-text-secondary">{footer}</div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

export const labelClass = "block text-[13px] font-medium text-text-secondary";

export const fieldClass =
  "mt-2 block w-full rounded-xl border border-border bg-surface px-4 py-3 text-sm text-text-primary shadow-sm transition-all duration-150 placeholder:text-text-muted hover:border-border-strong focus:border-accent focus:outline-none focus:ring-4 focus:ring-accent/15";

export const buttonClass =
  "inline-flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 py-3 text-sm font-semibold text-white shadow-sm transition-all duration-150 hover:bg-accent-strong hover:-translate-y-px hover:shadow-md active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:shadow-sm";

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{children}</p>
  );
}
