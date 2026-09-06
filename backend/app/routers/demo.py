"""Public marketing endpoint: demo requests.

UNAUTHENTICATED by design — this is a pre-signup sales lead, there is no session
and no organization yet. It does NOT use the RLS tenant-isolation pattern.

Compensating controls:
  * every field is validated here (pydantic + explicit checks) before it touches
    the database;
  * the `demo_requests` table has RLS on with no policies and no client grants,
    so the only path in is this endpoint, writing with the service-role key;
  * light size caps on every string.

No email notification is wired up yet — see the note in the response and
docs/PHASE-2.md. That is the obvious next step.
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..config import get_settings

router = APIRouter(tags=["marketing"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ORG_TYPES = {"clinic", "hospital", "medical_group"}

# common free / personal domains — this is a work-email form
_FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
}


class DemoRequestIn(BaseModel):
    organization_name: str = Field(min_length=1, max_length=200)
    work_email: str = Field(min_length=3, max_length=320)
    organization_type: str
    provider_count: int | None = Field(default=None, ge=0, le=100000)
    goals: str | None = Field(default=None, max_length=4000)
    source: str | None = Field(default=None, max_length=60)

    @field_validator("organization_name", "goals")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    @field_validator("organization_name")
    @classmethod
    def _org_not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("organization name is required")
        return v

    @field_validator("work_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("enter a valid email address")
        return v

    @field_validator("organization_type")
    @classmethod
    def _org_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in _ORG_TYPES:
            raise ValueError("organization type must be clinic, hospital, or medical_group")
        return v


CONFIRMATION = (
    "Thanks - your demo request is in. A member of the Foresight team will reach "
    "out at the email you provided to schedule a 30-minute walkthrough. We'll cover "
    "how the Commander coordinates Foresight's specialist AI agents; insurance "
    "eligibility verification and claims-risk detection before a claim is ever "
    "submitted; front-desk automation for scheduling, reminders and intake; the "
    "human approval controls that keep a person in charge of anything consequential; "
    "and how Foresight fits into the workflows your clinic or hospital already runs."
)


@router.post("/api/demo-requests", status_code=status.HTTP_201_CREATED)
async def create_demo_request(payload: DemoRequestIn, request: Request) -> dict:
    settings = get_settings()
    if not settings.supabase_service_role_key:
        raise HTTPException(status_code=500, detail="server is not configured to accept demo requests")

    domain = payload.work_email.rsplit("@", 1)[-1]
    row = {
        "organization_name": payload.organization_name,
        "work_email": payload.work_email,
        "organization_type": payload.organization_type,
        "provider_count": payload.provider_count,
        "goals": payload.goals or None,
        "source": payload.source or "marketing_site",
        "user_agent": request.headers.get("user-agent", "")[:400] or None,
    }

    async with httpx.AsyncClient(base_url=settings.rest_url, timeout=15.0) as client:
        resp = await client.post(
            "demo_requests",
            params={},
            json=row,
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail="could not record the demo request")

    created = resp.json()
    created = created[0] if isinstance(created, list) and created else created

    return {
        "id": created.get("id"),
        "personal_email_flagged": domain in _FREE_EMAIL_DOMAINS,
        "confirmation": CONFIRMATION,
        "covers": [
            "How Commander coordinates the specialist AI agents",
            "Insurance eligibility verification, before the visit",
            "Claims-risk detection, before a claim is submitted",
            "Front-desk automation: scheduling, reminders, intake",
            "Human approval controls for anything consequential",
            "Fitting Foresight into your existing clinic / hospital workflows",
        ],
    }
