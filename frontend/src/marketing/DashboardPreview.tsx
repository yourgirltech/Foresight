import { DashboardView } from "../components/dashboard/DashboardView";
import type { DashboardData } from "../lib/types";

/**
 * A non-interactive re-render of the REAL Overview page (the same DashboardView
 * component the authenticated app uses) with representative sample data, for the
 * marketing hero. Inert: `pointer-events-none`, no data fetch, no navigation.
 * Consumers frame / scale / clip it.
 */
const SAMPLE: DashboardData = {
  claims: {
    total: 128,
    submitted: 128,
    risk: { low: 96, medium: 22, high: 10, scored: 128 },
    escalated: 4,
    awaiting_approval: 14,
    at_risk: 14,
    missing_documentation: 6,
    missing_authorization: 5,
    clean_pct: 92,
  },
  eligibility: {
    total: 74,
    needs_followup: 12,
    verified_active: 55,
    verified_inactive: 5,
    check_failed: 2,
    emergency: 3,
  },
  prior_auth: {
    total: 31,
    needs_action: 7,
    required_draft: 4,
    authorized: 18,
    denied: 2,
    info_needed: 1,
    not_required: 9,
    emergency_exempt: 1,
  },
};

export function DashboardPreview({ width = 1100 }: { width?: number }) {
  return (
    <div
      aria-hidden
      style={{ width }}
      className="pointer-events-none max-w-none select-none bg-background p-5"
    >
      <DashboardView orgName="Riverside Family Medicine" data={SAMPLE} />
    </div>
  );
}
