import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import {
  BarChart3,
  Bell,
  CalendarDays,
  ChevronDown,
  ClipboardList,
  FileCheck,
  FileText,
  LayoutDashboard,
  type LucideIcon,
  Search,
  Settings,
  ShieldCheck,
  Users,
  UserRound,
} from "lucide-react";

import { useAuth } from "../auth/useAuth";
import { apiFetch } from "../lib/api";
import type { DashboardData } from "../lib/types";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  badge?: number;
}

const NAV: NavItem[] = [
  { to: "/app", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/app/patients", label: "Patients", icon: Users },
  { to: "/app/appointments", label: "Appointments", icon: CalendarDays },
  { to: "/app/insurance", label: "Insurance", icon: ShieldCheck },
  { to: "/app/prior-auth", label: "Prior Auth", icon: FileCheck },
  { to: "/app/claims", label: "Claims", icon: FileText },
  { to: "/app/front-desk", label: "Front Desk", icon: UserRound },
  { to: "/app/tasks", label: "Tasks", icon: ClipboardList, badge: 3 },
  { to: "/app/reports", label: "Reports", icon: BarChart3 },
  { to: "/app/settings", label: "Settings", icon: Settings },
];

function initials(name?: string | null): string {
  if (!name) return "–";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
}

function prettyRole(role?: string | null): string {
  if (!role) return "Member";
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function usePriorAuthBadge(): number | undefined {
  const [count, setCount] = useState<number | undefined>(undefined);
  useEffect(() => {
    let active = true;
    apiFetch<DashboardData>("/api/dashboard")
      .then((d) => {
        if (active) setCount(d.prior_auth?.needs_action || undefined);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);
  return count;
}

function Sidebar() {
  const priorAuthBadge = usePriorAuthBadge();
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface md:flex">
      {/* the wordmark always links back OUT to the public marketing site */}
      <Link to="/" className="flex items-center gap-2.5 px-5 py-5">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-white">
          <ShieldCheck className="h-5 w-5" strokeWidth={2.25} />
        </span>
        <span className="text-lg font-semibold tracking-tight text-text-primary">Foresight</span>
      </Link>

      <nav className="flex-1 space-y-1 px-3 py-2">
        {NAV.map(({ to, label, icon: Icon, end, badge: staticBadge }) => {
          const badge = to === "/app/prior-auth" ? priorAuthBadge : staticBadge;
          return (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              [
                "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-accent-soft text-accent"
                  : "text-text-secondary hover:bg-surface-muted hover:text-text-primary",
              ].join(" ")
            }
          >
            {({ isActive }) => (
              <>
                <Icon
                  className={[
                    "h-[18px] w-[18px] shrink-0",
                    isActive ? "text-accent" : "text-text-muted group-hover:text-text-secondary",
                  ].join(" ")}
                  strokeWidth={2}
                />
                <span className="flex-1">{label}</span>
                {badge ? (
                  <span className="grid h-5 min-w-[20px] place-items-center rounded-full bg-accent px-1 text-xs font-semibold text-white">
                    {badge}
                  </span>
                ) : null}
              </>
            )}
          </NavLink>
          );
        })}
      </nav>
    </aside>
  );
}

function OrgSwitcher() {
  const { organization, profile, signOut } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  async function handleSignOut() {
    await signOut();
    navigate("/login", { replace: true });
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-surface-muted"
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
          {initials(organization?.name)}
        </span>
        <span className="hidden leading-tight sm:block">
          <span className="block max-w-[160px] truncate text-sm font-medium text-text-primary">
            {organization?.name ?? "—"}
          </span>
          <span className="block text-xs text-text-secondary">{prettyRole(profile?.role)}</span>
        </span>
        <ChevronDown className="h-4 w-4 text-text-muted" />
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-2 w-64 overflow-hidden rounded-xl border border-border bg-surface shadow-lg">
          <div className="border-b border-border px-4 py-3">
            <p className="truncate text-sm font-medium text-text-primary">{organization?.name}</p>
            <p className="truncate text-xs text-text-secondary">{profile?.email}</p>
          </div>
          <button
            onClick={() => {
              setOpen(false);
              navigate("/app/settings");
            }}
            className="block w-full px-4 py-2.5 text-left text-sm text-text-secondary hover:bg-surface-muted hover:text-text-primary"
          >
            Settings
          </button>
          <button
            onClick={handleSignOut}
            className="block w-full px-4 py-2.5 text-left text-sm text-danger hover:bg-surface-muted"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function TopBar() {
  return (
    <header className="flex h-16 shrink-0 items-center gap-4 border-b border-border bg-surface px-4 sm:px-6">
      <div className="relative max-w-md flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
        <input
          type="search"
          placeholder="Search patients, claims, or tasks..."
          className="w-full rounded-lg border border-border bg-surface-muted py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:bg-surface focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>

      <button
        className="relative grid h-9 w-9 place-items-center rounded-lg text-text-secondary hover:bg-surface-muted hover:text-text-primary"
        aria-label="Notifications"
      >
        <Bell className="h-[18px] w-[18px]" />
        <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-danger ring-2 ring-surface" />
      </button>

      <OrgSwitcher />
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-screen bg-background">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
