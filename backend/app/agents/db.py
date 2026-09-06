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
