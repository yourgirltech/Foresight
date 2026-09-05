import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Session } from "@supabase/supabase-js";

import { supabase } from "../lib/supabase";
import type { Organization, ProfileWithOrg } from "../lib/types";

/**
 * Auth status is a small state machine the router keys off:
 *
 *   loading    -> still resolving session / profile; render nothing but a spinner
 *   signedOut  -> no session; only public routes
 *   noOrg      -> real session, but profile has no organization_id yet
 *                 -> onboarding or accept-invite only
 *   ready      -> session + resolved organization_id; the app shell may render
 */
export type AuthStatus = "loading" | "signedOut" | "noOrg" | "ready";

export interface AuthContextValue {
  status: AuthStatus;
  session: Session | null;
  profile: ProfileWithOrg | null;
  organization: Organization | null;
  refreshProfile: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [profile, setProfile] = useState<ProfileWithOrg | null>(null);
  const [sessionResolved, setSessionResolved] = useState(false);
  const [profileResolved, setProfileResolved] = useState(false);
  const currentUserId = useRef<string | null>(null);

  const loadProfile = useCallback(async (userId: string) => {
    setProfileResolved(false);
    const { data, error } = await supabase
      .from("profiles")
      .select("id, organization_id, role, email, full_name, organizations(*)")
      .eq("id", userId)
      .maybeSingle();

    if (error) {
      // eslint-disable-next-line no-console
      console.error("failed to load profile", error);
      setProfile(null);
    } else {
      setProfile((data as unknown as ProfileWithOrg) ?? null);
    }
    setProfileResolved(true);
  }, []);

  const refreshProfile = useCallback(async () => {
    if (currentUserId.current) {
      await loadProfile(currentUserId.current);
    }
  }, [loadProfile]);

  useEffect(() => {
    let active = true;

    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setSessionResolved(true);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setSessionResolved(true);
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  // React to identity changes: (re)load or clear the profile.
  useEffect(() => {
    const userId = session?.user.id ?? null;
    if (userId === currentUserId.current) return;
    currentUserId.current = userId;

    if (userId) {
      void loadProfile(userId);
    } else {
      setProfile(null);
      setProfileResolved(true);
    }
  }, [session, loadProfile]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    setProfile(null);
  }, []);

  const status: AuthStatus = useMemo(() => {
    if (!sessionResolved) return "loading";
    if (!session) return "signedOut";
    if (!profileResolved) return "loading";
    if (!profile?.organization_id) return "noOrg";
    return "ready";
  }, [sessionResolved, session, profileResolved, profile]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      session,
      profile,
      organization: profile?.organizations ?? null,
      refreshProfile,
      signOut,
    }),
    [status, session, profile, refreshProfile, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
