# Foresight frontend

React + TypeScript + Vite + Tailwind. Supabase Auth on the client; the app shell
renders **only** once there is a real session *and* a resolved `organization_id`.

## Auth state machine

`src/auth/AuthProvider.tsx` exposes a `status`:

| status | meaning | routes allowed |
|--------|---------|----------------|
| `loading` | resolving session/profile | spinner only |
| `signedOut` | no session | `/login`, `/signup` |
| `noOrg` | session, but `profiles.organization_id` is null | `/onboarding`, `/accept-invite` |
| `ready` | session + organization | app shell (`/`, `/team`) |

`RequireReady` mounts the shell; `RequireSession` gates onboarding/invite.

## Flows

- **Sign up** (`/signup`) → Supabase Auth → `noOrg`.
- **Onboarding** (`/onboarding`) → `rpc('bootstrap_organization')` → caller becomes
  `clinic_admin` of a new org → `ready`.
- **Invite** (`/team`, admins) → `rpc('create_invitation')` → share the
  `/accept-invite?token=…` link.
- **Accept** (`/accept-invite`) → sign up with the invited email →
  `rpc('accept_invitation')` → joins that org as `staff` → `ready`.

## Run

```bash
npm install
cp .env.example .env     # fill VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY from `npx supabase status`
npm run dev              # http://localhost:5173
```
