import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { AppShell } from "./AppShell";
import { FullPageSpinner } from "./Spinner";

/**
 * Gate for the app shell: only mounts children when there is a real session
 * AND a resolved organization_id. Anything short of that redirects — the shell
 * and its data never render on a half-resolved auth state.
 */
export function RequireReady() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <FullPageSpinner />;
  if (status === "signedOut") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  if (status === "noOrg") return <Navigate to="/onboarding" replace />;

  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

/** Gate for routes that need a session but not yet an organization. */
export function RequireSession() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <FullPageSpinner />;
  if (status === "signedOut") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
