import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/useAuth";

const nav = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/claims", label: "Claims", end: false },
  { to: "/team", label: "Team", end: false },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { organization, profile, signOut } = useAuth();
  const navigate = useNavigate();

  async function handleSignOut() {
    await signOut();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex h-full min-h-screen">
      <aside className="flex w-60 flex-col border-r border-slate-200 bg-white">
        <div className="px-5 py-5">
          <p className="text-lg font-semibold text-brand-700">Foresight</p>
          <p className="mt-1 truncate text-sm text-slate-500" title={organization?.name}>
            {organization?.name}
          </p>
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `block rounded-md px-3 py-2 text-sm font-medium ${
                  isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-200 px-5 py-4">
          <p className="truncate text-sm font-medium text-slate-700">{profile?.email}</p>
          <p className="text-xs uppercase tracking-wide text-slate-400">{profile?.role}</p>
          <button
            onClick={handleSignOut}
            className="mt-3 text-sm font-medium text-slate-500 hover:text-slate-900"
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-8 py-10">{children}</div>
      </main>
    </div>
  );
}
