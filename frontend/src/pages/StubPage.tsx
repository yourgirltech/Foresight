import { Construction } from "lucide-react";

/**
 * A themed placeholder for nav destinations that aren't built yet
 * (Patients, Insurance, Front Desk, Tasks, Reports). Keeps the sidebar honest
 * and clickable without 404s.
 */
export function StubPage({ title, blurb }: { title: string; blurb: string }) {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">{title}</h1>
      </header>
      <div className="rounded-xl border border-dashed border-border-strong bg-surface p-10 text-center">
        <span className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-surface-muted text-text-muted">
          <Construction className="h-6 w-6" />
        </span>
        <p className="mt-4 text-sm font-medium text-text-primary">Coming in a later phase</p>
        <p className="mx-auto mt-1 max-w-sm text-sm text-text-secondary">{blurb}</p>
      </div>
    </div>
  );
}
