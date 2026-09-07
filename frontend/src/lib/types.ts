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

// --- Eligibility verification (Phase 2) ----------------------------------

export type EligibilityStatus =
  | "pending"
  | "verified_active"
  | "verified_inactive"
  | "insufficient_info"
  | "check_failed";

export interface EligibilityCheck {
  id: string;
  appointment_id: string | null;
  previous_check_id: string | null;
  patient_name: string;
  patient_member_id: string;
  payer_id: string | null;
  payer_name: string | null;
  is_emergency: boolean;
  status: EligibilityStatus;
  result_payload: Record<string, unknown>;
  checked_at: string | null;
  created_at: string;
}

export interface Appointment {
  id: string;
  patient_name: string;
  patient_member_id: string;
  patient_dob: string | null;
  payer_id: string | null;
  scheduled_at: string | null;
  is_emergency: boolean;
  created_at: string;
}

export interface AppointmentWithCheck extends Appointment {
  latest_check: EligibilityCheck | null;
}

export interface EligibilityListResponse {
  organization_id: string;
  appointments: AppointmentWithCheck[];
  unscheduled_checks: EligibilityCheck[];
}

export interface AppointmentDetail {
  appointment: Appointment;
  payer: {
    id: string;
    name: string;
    eligibility_verification_supported: boolean;
    eligibility_active_threshold: number;
  } | null;
  checks: EligibilityCheck[];
  prior_authorizations: PriorAuthorization[];
  activity_log: ActivityEntry[];
}

export interface EligibilityCheckDetail {
  check: EligibilityCheck;
  chain: EligibilityCheck[];
  activity_log: ActivityEntry[];
}

export interface EligibilityDecisionResult {
  eligibility_check_id: string;
  appointment_id?: string | null;
  decision: { action: string; reason_code: string; route_to: string | null };
}

// --- Prior authorization (Phase 3) --------------------------------------

export type PriorAuthStatus =
  | "pending"
  | "not_required"
  | "emergency_exempt"
  | "insufficient_info"
  | "required_draft"
  | "submission_declined"
  | "submitting"
  | "submitted"
  | "auth_approved"
  | "info_needed"
  | "auth_denied";

export interface PriorAuthorization {
  id: string;
  appointment_id: string | null;
  previous_auth_id: string | null;
  patient_name: string;
  patient_member_id: string;
  payer_id: string | null;
  payer_name: string | null;
  procedure_code: string;
  procedure_description: string;
  place_of_service: string;
  is_emergency: boolean;
  status: PriorAuthStatus;
  determination_payload: Record<string, unknown>;
  request_payload: Record<string, unknown>;
  response_payload: Record<string, unknown>;
  authorization_number: string | null;
  determined_at: string | null;
  submitted_at: string | null;
  resolved_at: string | null;
  decided_by: string | null;
  created_at: string;
}

export interface PriorAuthListResponse {
  organization_id: string;
  prior_authorizations: PriorAuthorization[];
  needs_action: PriorAuthorization[];
}

export interface PriorAuthDetail {
  prior_authorization: PriorAuthorization;
  chain: PriorAuthorization[];
  activity_log: ActivityEntry[];
  appointment: Appointment | null;
}

export interface PriorAuthDecisionResult {
  prior_authorization_id?: string;
  decision: { action: string; reason_code: string; route_to: string | null };
}

// --- Dashboard (Overview) ------------------------------------------------

export interface DashboardData {
  claims: {
    total: number;
    submitted: number;
    risk: { low: number; medium: number; high: number; scored: number };
    escalated: number;
    awaiting_approval: number;
    at_risk: number;
    missing_documentation: number;
    missing_authorization: number;
    clean_pct: number;
  };
  eligibility: {
    total: number;
    needs_followup: number;
    verified_active: number;
    verified_inactive: number;
    check_failed: number;
    emergency: number;
  };
  prior_auth: {
    total: number;
    needs_action: number;
    required_draft: number;
    authorized: number;
    denied: number;
    info_needed: number;
    not_required: number;
    emergency_exempt: number;
  };
}

// --- Insurance card OCR (Phase 4 / 03) ---------------------------------

export type CardScanStatus =
  | "pending"
  | "extracted"
  | "needs_review"
  | "confirmed"
  | "rejected"
  | "error";

export type CardField = "member_id" | "group_number" | "payer_name" | "plan_type";
export type FieldConfidence = "high" | "medium" | "low";

export interface CardFieldMeta {
  confidence: FieldConfidence;
  legible: boolean;
  absent: boolean;
}

export interface CardScan {
  id: string;
  appointment_id: string | null;
  patient_name: string;
  image_path: string;
  image_mime: string;
  extracted_fields: Partial<Record<CardField, string | null>> & { error?: string };
  field_confidence: Partial<Record<CardField, CardFieldMeta>>;
  status: CardScanStatus;
  model: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  applied_to_appointment: boolean;
  image_retain_until: string | null;
  image_purged_at: string | null;
  created_at: string;
}

export interface CardScanListResponse {
  organization_id: string;
  card_scans: CardScan[];
  needs_review: CardScan[];
}

export interface CardScanDetail {
  card_scan: CardScan;
  image_url: string | null;
  appointment: Appointment | null;
}

export interface CardScanConfirmResult {
  card_scan: CardScan;
  applied_to_appointment: boolean;
  appointment_id: string | null;
  matched_payer_id: string | null;
  image_retain_until: string | null;
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
