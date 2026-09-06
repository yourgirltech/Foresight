import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";

/** Blue logo mark + wordmark. Always links to "/" (the marketing home). */
export function Logo({ withSubtitle = false }: { withSubtitle?: boolean }) {
  return (
    <Link to="/" className="flex shrink-0 items-start gap-2.5">
      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent text-white">
        <ShieldCheck className="h-5 w-5" strokeWidth={2.25} />
      </span>
      <span className="leading-tight">
        <span className="block text-lg font-semibold tracking-tight text-text-primary">
          Foresight
        </span>
        {withSubtitle && (
          <span className="hidden whitespace-nowrap text-[11px] font-medium text-text-muted sm:block">
            Anticipate. Automate. Advance Care.
          </span>
        )}
      </span>
    </Link>
  );
}

export const btnDark =
  "inline-flex items-center justify-center gap-2 rounded-lg bg-text-primary px-5 py-3 text-sm font-semibold text-surface transition-opacity hover:opacity-90";

export const btnOutline =
  "inline-flex items-center justify-center gap-2 rounded-lg border border-border-strong bg-surface px-5 py-3 text-sm font-semibold text-text-primary transition-colors hover:bg-surface-muted";

export const btnGhost =
  "inline-flex items-center gap-2 text-sm font-semibold text-text-secondary transition-colors hover:text-text-primary";

/** Constrained page section. */
export function Section({
  children,
  className = "",
  inner = "",
}: {
  children: ReactNode;
  className?: string;
  inner?: string;
}) {
  return (
    <section className={className}>
      <div className={`mx-auto w-full max-w-6xl px-6 sm:px-8 lg:px-10 ${inner}`}>{children}</div>
    </section>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent/80">{children}</p>
  );
}

/** Marketing headline — Fraunces (font-display). Use for hero / section titles only. */
export function DisplayHeading({
  as: Tag = "h2",
  className = "",
  children,
}: {
  as?: "h1" | "h2" | "h3";
  className?: string;
  children: ReactNode;
}) {
  return (
    <Tag
      className={`font-display font-semibold tracking-tight text-text-primary ${className}`}
    >
      {children}
    </Tag>
  );
}

/**
 * Page hero used by every content page. Textured background, an accent glow,
 * an optional right-hand visual, and optional facet chips.
 */
export function PageHeader({
  eyebrow,
  title,
  intro,
  chips,
  visual,
}: {
  eyebrow: string;
  title: ReactNode;
  intro: string;
  chips?: string[];
  visual?: ReactNode;
}) {
  return (
    <Section
      className="relative overflow-hidden border-b border-border bg-surface"
      inner="py-20 lg:py-28"
    >
      <div className="fp-dotgrid pointer-events-none absolute inset-0 opacity-50" />
      <div className="pointer-events-none absolute -left-40 -top-40 h-96 w-96 rounded-full bg-accent/10 blur-3xl" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-surface to-transparent" />

      <div
        className={`relative grid items-center gap-14 ${
          visual ? "lg:grid-cols-[1.05fr_0.95fr] lg:gap-16" : ""
        }`}
      >
        <div className={visual ? "min-w-0" : "max-w-3xl"}>
          <div className="flex items-center gap-3">
            <span className="h-px w-8 bg-accent" />
            <Eyebrow>{eyebrow}</Eyebrow>
          </div>
          <DisplayHeading as="h1" className="mt-6 text-4xl leading-[1.12] sm:text-5xl">
            {title}
          </DisplayHeading>
          <p className="mt-6 max-w-xl text-lg leading-[1.75] text-text-secondary">{intro}</p>
          {chips && chips.length > 0 && (
            <div className="mt-8 flex flex-wrap gap-2">
              {chips.map((c) => (
                <span
                  key={c}
                  className="rounded-full border border-border bg-background px-3 py-1 text-xs font-medium text-text-secondary"
                >
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
        {visual && <div className="relative hidden lg:block">{visual}</div>}
      </div>
    </Section>
  );
}

/** The recurring "ready to talk?" panel at the foot of a marketing page. */
export function CtaPanel({
  title,
  body,
  children,
}: {
  title: ReactNode;
  body?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mt-20 rounded-2xl border border-border bg-surface px-8 py-14 text-center lg:py-16">
      <DisplayHeading as="h2" className="text-2xl sm:text-[1.75rem]">
        {title}
      </DisplayHeading>
      {body && (
        <p className="mx-auto mt-4 max-w-lg text-sm leading-[1.75] text-text-secondary">{body}</p>
      )}
      <div className="mt-9 flex flex-wrap justify-center gap-4">{children}</div>
    </div>
  );
}

export function FeatureCard({
  icon: Icon,
  title,
  children,
}: {
  icon: (props: { className?: string; strokeWidth?: number }) => ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="group rounded-2xl border border-border bg-surface p-7 transition-all duration-300 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_18px_40px_-20px_rgb(var(--color-text-primary)/0.22)]">
      <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent transition-colors group-hover:bg-accent group-hover:text-white">
        <Icon className="h-5 w-5" strokeWidth={2} />
      </span>
      <h3 className="mt-5 text-base font-semibold text-text-primary">{title}</h3>
      <p className="mt-3 text-sm leading-[1.7] text-text-secondary">{children}</p>
    </div>
  );
}

/** A small labelled figure for the enriched content pages. */
export function MiniStat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div>
      <p className="font-display text-2xl font-semibold text-text-primary">{value}</p>
      <p className="mt-1 text-xs text-text-secondary">{label}</p>
    </div>
  );
}

/** Numbered / lettered marker used in walkthroughs. */
export function StepMarker({ children }: { children: ReactNode }) {
  return (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent text-sm font-semibold text-white">
      {children}
    </span>
  );
}
