import { useCallback, useEffect, useState, type FormEvent } from "react";

import { useAuth } from "../auth/useAuth";
import { supabase } from "../lib/supabase";
import type { Invitation, Profile, Role } from "../lib/types";

export function TeamPage() {
  const { profile, organization } = useAuth();
  const isAdmin = profile?.role === "clinic_admin";

  const [members, setMembers] = useState<Profile[]>([]);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("staff");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    // Both queries are RLS-scoped to the caller's organization.
    const [m, i] = await Promise.all([
      supabase.from("profiles").select("id, organization_id, role, email, full_name").order("created_at"),
      supabase
        .from("invitations")
        .select("id, organization_id, email, role, status, token, created_at, accepted_at")
        .order("created_at", { ascending: false }),
    ]);
    if (m.error) setError(m.error.message);
    else setMembers(m.data as Profile[]);
    if (!i.error) setInvites(i.data as Invitation[]);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const { error } = await supabase.rpc("create_invitation", {
      invitee_email: email,
      invitee_role: role,
    });
    setBusy(false);
    if (error) {
      setError(error.message);
      return;
    }
    setEmail("");
    await load();
  }

  async function revoke(id: string) {
    const { error } = await supabase.rpc("revoke_invitation", { invitation_id: id });
    if (error) setError(error.message);
    await load();
  }

  const inviteLink = (token: string) => `${window.location.origin}/accept-invite?token=${token}`;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Team</h1>
        <p className="mt-1 text-sm text-slate-500">{organization?.name}</p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-700">
          Members ({members.length})
        </div>
        <ul className="divide-y divide-slate-100">
          {members.map((m) => (
            <li key={m.id} className="flex items-center justify-between px-5 py-3 text-sm">
              <span className="text-slate-700">{m.email}</span>
              <span className="text-xs uppercase tracking-wide text-slate-400">{m.role}</span>
            </li>
          ))}
        </ul>
      </section>

      {isAdmin && (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">Invite a teammate</h2>
          <form onSubmit={handleInvite} className="mt-3 flex flex-wrap items-end gap-3">
            <label className="text-sm font-medium text-slate-600">
              Email
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 block w-64 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />
            </label>
            <label className="text-sm font-medium text-slate-600">
              Role
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              >
                <option value="staff">staff</option>
                <option value="clinic_admin">clinic_admin</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              {busy ? "Inviting…" : "Send invite"}
            </button>
          </form>
          {error && (
            <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
          )}
        </section>
      )}

      {isAdmin && invites.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-700">
            Invitations
          </div>
          <ul className="divide-y divide-slate-100">
            {invites.map((inv) => (
              <li key={inv.id} className="px-5 py-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-slate-700">{inv.email}</span>
                  <span className="text-xs uppercase tracking-wide text-slate-400">
                    {inv.role} · {inv.status}
                  </span>
                </div>
                {inv.status === "pending" && (
                  <div className="mt-2 flex items-center gap-3">
                    <input
                      readOnly
                      value={inviteLink(inv.token)}
                      onFocus={(e) => e.currentTarget.select()}
                      className="flex-1 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-500"
                    />
                    <button
                      onClick={() => navigator.clipboard?.writeText(inviteLink(inv.token))}
                      className="text-xs font-medium text-brand-600 hover:text-brand-700"
                    >
                      Copy
                    </button>
                    <button
                      onClick={() => revoke(inv.id)}
                      className="text-xs font-medium text-slate-400 hover:text-red-600"
                    >
                      Revoke
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
