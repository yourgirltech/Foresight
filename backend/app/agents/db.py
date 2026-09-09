"""Service-role PostgREST access for the agent system.

Agent processing is a background job, not a user request, so it runs with the
service-role key (it must write activity_log / escalations rows the end user
cannot). Per docs/architecture.md §3.4 this is the "batch job" carve-out.

The compensating control: **every method here takes `org_id` explicitly and
filters on it.** `org_id` is resolved once, from the triggering claim row, by
the orchestrator — never from a request body, env var, or constant. There is no
method that reads or writes a tenant-scoped table without an
`organization_id=eq.<org>` filter. tests/agent_isolation_test.py proves a run
over one clinic's claim never touches another clinic's rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import get_settings


def _client() -> httpx.AsyncClient:
    s = get_settings()
    if not s.supabase_service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set — the agent system cannot run")
    return httpx.AsyncClient(
        base_url=s.rest_url,
        headers={
            "apikey": s.supabase_service_role_key,
            "Authorization": f"Bearer {s.supabase_service_role_key}",
            "Accept": "application/json",
        },
        timeout=15.0,
    )


async def _get(path: str, params: dict[str, Any]) -> list[dict]:
    async with _client() as c:
        r = await c.get(path.lstrip("/"), params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]


async def _write(method: str, path: str, params: dict[str, Any], body: Any) -> list[dict]:
    async with _client() as c:
        r = await c.request(
            method,
            path.lstrip("/"),
            params=params,
            json=body,
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
        )
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return []
        data = r.json()
        return data if isinstance(data, list) else [data]


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
async def get_claim(claim_pk: str) -> dict | None:
    """Look up a claim by primary key. Used ONCE by the orchestrator to resolve
    the organization_id that scopes everything else."""
    rows = await _get("/claims", {"id": f"eq.{claim_pk}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def get_payer(org_id: str, payer_id: str) -> dict | None:
    rows = await _get(
        "/payers",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{payer_id}", "select": "*", "limit": 1},
    )
    return rows[0] if rows else None


async def list_issues(org_id: str, claim_pk: str) -> list[dict]:
    return await _get(
        "/claim_issues",
        {
            "organization_id": f"eq.{org_id}",
            "claim_id": f"eq.{claim_pk}",
            "select": "issue_type,severity,description,evidence",
            "order": "created_at",
        },
    )


async def latest_recommendation(org_id: str, claim_pk: str) -> dict | None:
    rows = await _get(
        "/recommendations",
        {
            "organization_id": f"eq.{org_id}",
            "claim_id": f"eq.{claim_pk}",
            "select": "*",
            "order": "created_at.desc",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def list_recommendations(org_id: str, claim_pk: str) -> list[dict]:
    return await _get(
        "/recommendations",
        {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{claim_pk}",
         "select": "*", "order": "created_at"},
    )


async def list_follow_ups(org_id: str, claim_pk: str) -> list[dict]:
    return await _get(
        "/follow_ups",
        {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{claim_pk}",
         "select": "*", "order": "created_at"},
    )


async def list_activity(org_id: str, claim_pk: str) -> list[dict]:
    return await _get(
        "/activity_log",
        {
            "organization_id": f"eq.{org_id}",
            "claim_id": f"eq.{claim_pk}",
            "select": "actor,action,details,created_at",
            "order": "created_at",
        },
    )


# --------------------------------------------------------------------------- #
# Phase 2 — eligibility verification reads
# --------------------------------------------------------------------------- #
async def get_appointment(org_id: str, appt_id: str) -> dict | None:
    rows = await _get(
        "/appointments",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{appt_id}", "select": "*", "limit": 1},
    )
    return rows[0] if rows else None


async def get_eligibility_check(check_id: str) -> dict | None:
    """Look up an eligibility check by primary key. Used ONCE by the orchestrator
    to resolve the organization_id that scopes everything else — the eligibility
    analogue of get_claim()."""
    rows = await _get(
        "/eligibility_checks", {"id": f"eq.{check_id}", "select": "*", "limit": 1}
    )
    return rows[0] if rows else None


async def list_eligibility_checks(org_id: str, appointment_id: str) -> list[dict]:
    return await _get(
        "/eligibility_checks",
        {
            "organization_id": f"eq.{org_id}",
            "appointment_id": f"eq.{appointment_id}",
            "select": "*",
            "order": "created_at.desc",
        },
    )


# --------------------------------------------------------------------------- #
# writes — all scoped by org_id
# --------------------------------------------------------------------------- #
async def update_claim(org_id: str, claim_pk: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH",
        "/claims",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{claim_pk}"},
        fields,
    )
    return rows[0] if rows else None


async def replace_issues(org_id: str, claim_pk: str, issues: list[dict]) -> list[dict]:
    """Clear any prior issues for this claim (reanalyze is idempotent) and insert
    the fresh set."""
    async with _client() as c:
        r = await c.request(
            "DELETE",
            "claim_issues",
            params={"organization_id": f"eq.{org_id}", "claim_id": f"eq.{claim_pk}"},
        )
        r.raise_for_status()
    if not issues:
        return []
    rows = [
        {
            "organization_id": org_id,
            "claim_id": claim_pk,
            "issue_type": i["issue_type"],
            "severity": i["severity"],
            "description": i["description"],
            "evidence": i.get("evidence", {}),
        }
        for i in issues
    ]
    return await _write("POST", "/claim_issues", {}, rows)


async def set_recommendation_decision(
    org_id: str, rec_id: str, *, approval_status: str, decided_by: str | None
) -> dict | None:
    """Record a human approve/decline. Used by the seed and e2e test; the
    approve/decline endpoint does the equivalent under the caller's own RLS."""
    rows = await _write(
        "PATCH",
        "/recommendations",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{rec_id}"},
        {
            "approval_status": approval_status,
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "decided_by": decided_by,
        },
    )
    return rows[0] if rows else None


async def insert_recommendation(org_id: str, claim_pk: str, rec: dict) -> dict:
    rows = await _write(
        "POST",
        "/recommendations",
        {},
        {
            "organization_id": org_id,
            "claim_id": claim_pk,
            "action_type": rec["action_type"],
            "confidence": rec["confidence"],
            "low_confidence": rec["low_confidence"],
            "rationale": rec["rationale"],
            "cited_issue_types": rec.get("cited_issue_types", []),
        },
    )
    return rows[0]


async def insert_follow_up(
    org_id: str,
    claim_pk: str,
    *,
    kind: str,
    note: str,
    due_at: str,
    originating_agent: str,
    sent_at: str | None,
) -> dict:
    rows = await _write(
        "POST",
        "/follow_ups",
        {},
        {
            "organization_id": org_id,
            "claim_id": claim_pk,
            "kind": kind,
            "note": note,
            "due_at": due_at,
            "originating_agent": originating_agent,
            "simulated_send": True,
            "sent_at": sent_at,
        },
    )
    return rows[0]


async def insert_escalation(
    org_id: str,
    claim_pk: str | None,
    *,
    reason_code: str,
    originating_agent: str,
    context: dict,
    appointment_id: str | None = None,
    eligibility_check_id: str | None = None,
    prior_authorization_id: str | None = None,
    voice_reminder_id: str | None = None,
) -> dict:
    rows = await _write(
        "POST",
        "/escalations",
        {},
        {
            "organization_id": org_id,
            "claim_id": claim_pk,
            "reason_code": reason_code,
            "originating_agent": originating_agent,
            "context": context,
            "appointment_id": appointment_id,
            "eligibility_check_id": eligibility_check_id,
            "prior_authorization_id": prior_authorization_id,
            "voice_reminder_id": voice_reminder_id,
        },
    )
    return rows[0]


async def insert_activity(
    org_id: str,
    claim_pk: str | None,
    *,
    actor: str,
    action: str,
    details: dict,
    appointment_id: str | None = None,
    eligibility_check_id: str | None = None,
    prior_authorization_id: str | None = None,
    voice_reminder_id: str | None = None,
    patient_contact_id: str | None = None,
) -> dict:
    rows = await _write(
        "POST",
        "/activity_log",
        {},
        {
            "organization_id": org_id,
            "claim_id": claim_pk,
            "actor": actor,
            "action": action,
            "details": details,
            "appointment_id": appointment_id,
            "eligibility_check_id": eligibility_check_id,
            "prior_authorization_id": prior_authorization_id,
            "voice_reminder_id": voice_reminder_id,
            "patient_contact_id": patient_contact_id,
        },
    )
    return rows[0]


# --------------------------------------------------------------------------- #
# Phase 2 — eligibility verification writes (all scoped by org_id)
# --------------------------------------------------------------------------- #
async def insert_appointment(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/appointments", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def insert_eligibility_check(org_id: str, fields: dict) -> dict:
    rows = await _write(
        "POST", "/eligibility_checks", {}, {**fields, "organization_id": org_id}
    )
    return rows[0]


async def update_eligibility_check(org_id: str, check_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH",
        "/eligibility_checks",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{check_id}"},
        fields,
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Phase 3 — prior authorization (all scoped by org_id)
# --------------------------------------------------------------------------- #
async def get_prior_authorization(pa_id: str) -> dict | None:
    """Look up a prior_authorizations row by primary key. Used ONCE by the
    orchestrator to resolve the organization_id that scopes everything else —
    the prior-auth analogue of get_claim() / get_eligibility_check()."""
    rows = await _get(
        "/prior_authorizations", {"id": f"eq.{pa_id}", "select": "*", "limit": 1}
    )
    return rows[0] if rows else None


async def list_prior_authorizations(org_id: str, appointment_id: str) -> list[dict]:
    return await _get(
        "/prior_authorizations",
        {
            "organization_id": f"eq.{org_id}",
            "appointment_id": f"eq.{appointment_id}",
            "select": "*",
            "order": "created_at.desc",
        },
    )


async def insert_prior_authorization(org_id: str, fields: dict) -> dict:
    rows = await _write(
        "POST", "/prior_authorizations", {}, {**fields, "organization_id": org_id}
    )
    return rows[0]


async def update_prior_authorization(org_id: str, pa_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH",
        "/prior_authorizations",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{pa_id}"},
        fields,
    )
    return rows[0] if rows else None


async def update_appointment(org_id: str, appt_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH",
        "/appointments",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{appt_id}"},
        fields,
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Phase 4 — insurance card OCR (03). Sidecar table; NOT a Commander agent.
# All writes scoped by org_id, resolved from the verified session.
# --------------------------------------------------------------------------- #
async def get_card_scan(scan_id: str) -> dict | None:
    rows = await _get("/card_scans", {"id": f"eq.{scan_id}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def insert_card_scan(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/card_scans", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def update_card_scan(org_id: str, scan_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH",
        "/card_scans",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{scan_id}"},
        fields,
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Phase 4 — coordination of benefits (04). Plain data table; NOT a Commander
# agent. determine_cob() reads and computes; it never writes. All writes scoped
# by org_id, resolved from the verified session.
# --------------------------------------------------------------------------- #
async def get_patient_coverage(coverage_id: str) -> dict | None:
    rows = await _get("/patient_coverages", {"id": f"eq.{coverage_id}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def insert_patient_coverage(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/patient_coverages", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def update_patient_coverage(org_id: str, coverage_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH",
        "/patient_coverages",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{coverage_id}"},
        fields,
    )
    return rows[0] if rows else None


async def delete_patient_coverage(org_id: str, coverage_id: str) -> None:
    await _write(
        "DELETE",
        "/patient_coverages",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{coverage_id}"},
        None,
    )


# --------------------------------------------------------------------------- #
# Phase 4 — cost estimate / Good Faith Estimate (05). NOT a Commander agent.
# price() computes the number; this only stores the finished document. Scoped
# by org_id, resolved from the verified session.
# --------------------------------------------------------------------------- #
async def get_cost_estimate(estimate_id: str) -> dict | None:
    rows = await _get("/cost_estimates", {"id": f"eq.{estimate_id}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def insert_cost_estimate(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/cost_estimates", {}, {**fields, "organization_id": org_id})
    return rows[0]


# --------------------------------------------------------------------------- #
# Phase 5 — appeals (11). A fourth disjoint Commander family. Appeals are
# claim-scoped: activity_log / escalations reuse claim_id (with context.appeal_id).
# All writes scoped by org_id, resolved once from the triggering claim.
# --------------------------------------------------------------------------- #
async def get_appeal(appeal_id: str) -> dict | None:
    rows = await _get("/appeals", {"id": f"eq.{appeal_id}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def latest_appeal(org_id: str, claim_pk: str) -> dict | None:
    rows = await _get(
        "/appeals",
        {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{claim_pk}",
         "select": "*", "order": "created_at.desc", "limit": 1},
    )
    return rows[0] if rows else None


async def list_appeals(org_id: str, claim_pk: str) -> list[dict]:
    return await _get(
        "/appeals",
        {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{claim_pk}",
         "select": "*", "order": "created_at"},
    )


async def insert_appeal(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/appeals", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def update_appeal(org_id: str, appeal_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH", "/appeals",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{appeal_id}"},
        fields,
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Phase 6 — voice appointment reminders (agent 17, an n8n workflow).
#
# The /due list query spans clinics BY DESIGN (it is the cross-clinic dispatch
# queue). Every per-row write below is org-scoped from the voice_reminders row.
# docs/agents/17-voice-reminder-agent.md §2.3.
# --------------------------------------------------------------------------- #
async def get_organization(org_id: str) -> dict | None:
    rows = await _get("/organizations", {"id": f"eq.{org_id}", "select": "id,name,timezone", "limit": 1})
    return rows[0] if rows else None


async def get_voice_reminder(vr_id: str) -> dict | None:
    """By pk. Used ONCE to resolve organization_id for the /outcome and
    mark-calling paths — the get_claim() analogue."""
    rows = await _get("/voice_reminders", {"id": f"eq.{vr_id}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def voice_reminder_by_call_id(vapi_call_id: str) -> dict | None:
    """The ONLY join key the webhook/outcome path trusts (§2.4)."""
    rows = await _get(
        "/voice_reminders", {"vapi_call_id": f"eq.{vapi_call_id}", "select": "*", "limit": 1}
    )
    return rows[0] if rows else None


async def list_due_voice_reminders(*, before_iso: str, limit: int) -> list[dict]:
    """status = pending, scheduled_call_at <= now, authorized. Cross-clinic."""
    return await _get(
        "/voice_reminders",
        {
            "status": "eq.pending",
            "scheduled_call_at": f"lte.{before_iso}",
            "authorized_by": "not.is.null",
            "select": "*",
            "order": "scheduled_call_at.asc",
            "limit": limit,
        },
    )


async def list_stale_dispatching(*, dispatched_before_iso: str, limit: int = 100) -> list[dict]:
    return await _get(
        "/voice_reminders",
        {
            "status": "eq.dispatching",
            "dispatched_at": f"lt.{dispatched_before_iso}",
            "select": "*",
            "order": "dispatched_at.asc",
            "limit": limit,
        },
    )


async def update_voice_reminder(org_id: str, vr_id: str, fields: dict) -> dict | None:
    rows = await _write(
        "PATCH", "/voice_reminders",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{vr_id}"},
        fields,
    )
    return rows[0] if rows else None


async def insert_voice_reminder(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/voice_reminders", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def get_appointment_any_org(appt_id: str) -> dict | None:
    rows = await _get("/appointments", {"id": f"eq.{appt_id}", "select": "*", "limit": 1})
    return rows[0] if rows else None


async def get_patient_contact(org_id: str, contact_id: str) -> dict | None:
    rows = await _get(
        "/patient_contacts",
        {"organization_id": f"eq.{org_id}", "id": f"eq.{contact_id}", "select": "*", "limit": 1},
    )
    return rows[0] if rows else None


async def list_patient_consents(org_id: str, contact_id: str) -> list[dict]:
    return await _get(
        "/patient_consents",
        {
            "organization_id": f"eq.{org_id}",
            "patient_contact_id": f"eq.{contact_id}",
            "select": "*",
            "order": "recorded_at.asc",
        },
    )


async def insert_patient_contact(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/patient_contacts", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def insert_patient_consent(org_id: str, fields: dict) -> dict:
    rows = await _write("POST", "/patient_consents", {}, {**fields, "organization_id": org_id})
    return rows[0]


async def find_patient_contacts(org_id: str, *, patient_name: str, patient_dob: str | None) -> list[dict]:
    params = {
        "organization_id": f"eq.{org_id}",
        "patient_name": f"eq.{patient_name}",
        "select": "*",
        "order": "created_at.desc",
    }
    if patient_dob:
        params["patient_dob"] = f"eq.{patient_dob}"
    return await _get("/patient_contacts", params)
