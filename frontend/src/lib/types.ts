export type Role = "platform_admin" | "clinic_admin" | "staff";

export interface Organization {
  id: string;
  name: string;
  created_at: string;
}

export interface Profile {
  id: string;
  organization_id: string | null;
  role: Role | null;
  email: string;
  full_name: string | null;
}

export interface ProfileWithOrg extends Profile {
  organizations: Organization | null;
}

// --- Claims & billing (Phase 1) -------------------------------------------

export type ClaimStatus =
  | "received"
  | "analyzed"
  | "cleared"
  | "reasoned"
  | "awaiting_approval"
  | "executing"
  | "actioned"
  | "manual_action_required"
  | "declined"
  | "escalated"
  | "denied"
  | "paid"
  | "rejected";

export type RiskLevel = "Low" | "Medium" | "High";
export type IssueType =
  | "missing_authorization"
  | "missing_documentation"
  | "code_mismatch"
  | "overdue_follow_up";
export type IssueSeverity = "low" | "medium" | "high";
export type ConfidenceBand = "High" | "Medium" | "Low";
export type ApprovalStatus = "pending" | "approved" | "declined";
export type RecommendationAction =
  | "submit_authorization_request"
  | "request_documentation"
  | "payer_status_follow_up"
  | "resubmit_corrected_coding";

export interface ClaimSummary {
  id: string;
  claim_id: string;
  patient_name: string;
  amount: string | number;
  status: ClaimStatus;
  risk_score: number;
  risk_level: RiskLevel | null;
  payer_name: string | null;
  created_at: string;
}

export interface ClaimIssue {
  issue_type: IssueType;
  severity: IssueSeverity;
  description: string;
  evidence: Record<string, unknown>;
  created_at: string;
}

export interface Recommendation {
  id: string;
  action_type: RecommendationAction;
  confidence: ConfidenceBand;
  low_confidence: boolean;
  rationale: string;
  cited_issue_types: IssueType[];
  approval_status: ApprovalStatus;
  decided_at: string | null;
  decided_by: string | null;
  created_at: string;
}

export interface ActivityEntry {
  actor: string;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Escalation {
  reason_code: string;
  originating_agent: string;
  context: Record<string, unknown>;
  created_at: string;
}

export interface FollowUp {
  kind: "follow_up" | "payer_reminder";
  note: string;
  due_at: string;
  originating_agent: string;
  simulated_send: boolean;
  sent_at: string | null;
  created_at: string;
}

export interface ClaimPayer {
  id: string;
  name: string;
  authorization_required: boolean;
  documentation_required: boolean;
  follow_up_threshold_days: number | null;
}

export interface ClaimDetail {
  claim: {
    id: string;
    claim_id: string;
    patient_name: string;
    patient_member_id: string;
    amount: string | number;
    status: ClaimStatus;
    risk_score: number;
    risk_level: RiskLevel | null;
    authorization_present: boolean;
    documentation_present: boolean;
    coding_matches: boolean;
    last_followup_at: string | null;
    reasoning_summary: string | null;
    reasoning_detail: { issue_type: string; explanation: string }[] | null;
    reasoning_generated_at: string | null;
    created_at: string;
  };
  payer: ClaimPayer | null;
  issues: ClaimIssue[];
  recommendations: Recommendation[];
  recommendation: Recommendation | null;
  activity_log: ActivityEntry[];
  escalations: Escalation[];
  follow_ups: FollowUp[];
}

export interface DecisionResult {
  claim_id: string;
  status: ClaimStatus;
  decision: { action: string; reason_code: string; route_to: string | null };
}

export type InvitationStatus = "pending" | "accepted" | "revoked";

export interface Invitation {
  id: string;
  organization_id: string;
  email: string;
  role: Role;
  status: InvitationStatus;
  token: string;
  created_at: string;
  accepted_at: string | null;
}
