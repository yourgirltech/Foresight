import { Link } from "react-router-dom";
import { Check, Monitor, Moon, Sun, Users } from "lucide-react";

import { useTheme } from "../theme/ThemeProvider";

const THEME_ICON: Record<string, typeof Sun> = {
  light: Sun,
  dark: Moon,
};

export function SettingsPage() {
  const { theme, themes, setTheme } = useTheme();

  return (
    <div className="max-w-3xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-text-primary">Settings</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Preferences for your account and this facility.
        </p>
      </header>

      {/* Appearance */}
      <section className="rounded-xl border border-border bg-surface p-5">
        <h2 className="text-sm font-semibold text-text-primary">Appearance</h2>
        <p className="mt-1 text-xs text-text-secondary">
          Choose a theme. Defaults to your device's light/dark setting, and your choice is
          remembered on this browser.
        </p>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {themes.map((t) => {
            const Icon = THEME_ICON[t.name] ?? Monitor;
            const active = t.name === theme;
            return (
              <button
                key={t.name}
                onClick={() => setTheme(t.name)}
                aria-pressed={active}
                className={[
                  "flex items-start gap-3 rounded-lg border p-4 text-left transition-colors",
                  active
                    ? "border-accent bg-accent-soft"
                    : "border-border bg-surface hover:bg-surface-muted",
                ].join(" ")}
              >
                <span
                  className={[
                    "grid h-9 w-9 shrink-0 place-items-center rounded-full",
                    active ? "bg-accent text-white" : "bg-surface-muted text-text-secondary",
                  ].join(" ")}
                >
                  <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
                    {t.label}
                    {active && <Check className="h-4 w-4 text-accent" />}
                  </span>
                  <span className="mt-0.5 block text-xs text-text-secondary">{t.description}</span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Team — kept reachable from Settings */}
      <section className="rounded-xl border border-border bg-surface p-5">
        <h2 className="text-sm font-semibold text-text-primary">Team</h2>
        <p className="mt-1 text-xs text-text-secondary">
          Manage the people in your facility and pending invitations.
        </p>
        <Link
          to="/app/team"
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-medium text-text-secondary hover:bg-surface-muted"
        >
          <Users className="h-4 w-4 text-text-muted" />
          Open team management
        </Link>
      </section>
    </div>
  );
}
