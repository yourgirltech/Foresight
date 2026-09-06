import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth/useAuth";
import { DashboardView } from "../components/dashboard/DashboardView";
import { apiFetch } from "../lib/api";
import type { DashboardData } from "../lib/types";

export function DashboardPage() {
  const { organization } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<DashboardData>("/api/dashboard")
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <DashboardView
      orgName={organization?.name}
      data={data}
      error={error}
      onNavigate={(to) => navigate(to)}
    />
  );
}
