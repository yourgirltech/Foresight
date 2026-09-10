"""Cross-agent aggregates for the Patients / Insurance / Tasks / Reports views.

Every number and row here is derived from the caller's own RLS-scoped tables
(their JWT is forwarded to PostgREST) — no service-role reads, no invented data.
Where a metric has no backing table yet it is simply omitted; the frontend
labels those as "not built".
"""
from __future__ import annotations

from collections import Counter, defaultdict

from fastapi import APIRouter, Depends

from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["insights"])


def _current_chain(rows: list[dict], prev_key: str) -> list[dict]:
    superseded = {r[prev_key] for r in rows if r.get(prev_key)}
    return [r for r in rows if r["id"] not in superseded]


# --------------------------------------------------------------------------- #
# GET /api/tasks — everything the AI has surfaced for a human, in one queue
# --------------------------------------------------------------------------- #
@router.get("/api/tasks")
async def tasks(ctx: AuthContext = Depends(require_organization)) -> dict:
    t = ctx.access_token
    escalations = await rest_get(
        t, "/escalations",
        {"select": "id,reason_code,originating_agent,context,claim_id,appointment_id,"
                   "prior_authorization_id,voice_reminder_id,created_at",
         "order": "created_at.desc"},
    )
    claims = await rest_get(
        t, "/claims",
        {"select": "id,claim_id,patient_name,amount,status,risk_level", "order": "created_at.desc"},
    )
    recs = await rest_get(
        t, "/recommendations",
        {"approval_status": "eq.pending", "select": "id,claim_id,action_type,confidence,low_confidence,created_at",
         "order": "created_at.desc"},
    )
    pas = await rest_get(
        t, "/prior_authorizations",
        {"select": "id,patient_name,procedure_code,status,previous_auth_id,appointment_id,created_at",
         "order": "created_at.desc"},
    )
    appeals = await rest_get(
        t, "/appeals",
        {"select": "id,claim_id,status,previous_appeal_id,created_at", "order": "created_at.desc"},
    )
    reminders = await rest_get(
        t, "/voice_reminders",
        {"select": "id,appointment_id,patient_name_snapshot,status,outcome,created_at",
         "order": "created_at.desc"},
    )

    claim_by_id = {c["id"]: c for c in claims}
    items: list[dict] = []

    for e in escalations:
        link = None
        if e.get("claim_id"):
            link = f"/app/claims/{e['claim_id']}"
        elif e.get("appointment_id"):
            link = f"/app/appointments/{e['appointment_id']}"
        elif e.get("prior_authorization_id"):
            link = f"/app/prior-auth/{e['prior_authorization_id']}"
        items.append({
            "id": f"esc:{e['id']}", "kind": "escalation",
            "title": (e["reason_code"] or "escalation").replace("_", " "),
            "detail": f"raised by {e.get('originating_agent') or 'the system'}",
            "severity": "high", "link": link, "created_at": e["created_at"],
        })

    for r in recs:
        c = claim_by_id.get(r.get("claim_id"), {})
        if c.get("status") != "awaiting_approval":
            continue
        items.append({
            "id": f"rec:{r['id']}", "kind": "approval",
            "title": f"Approve or decline: {r['action_type'].replace('_', ' ')}",
            "detail": f"{c.get('claim_id', 'claim')} · {c.get('patient_name', '')} · {r['confidence']} confidence",
            "severity": "medium", "link": f"/app/claims/{r['claim_id']}", "created_at": r["created_at"],
        })

    for p in _current_chain(pas, "previous_auth_id"):
        if p["status"] not in ("required_draft", "auth_denied", "info_needed"):
            continue
        label = {"required_draft": "Prior auth drafted — review & submit",
                 "auth_denied": "Prior auth denied — peer-to-peer / appeal",
                 "info_needed": "Prior auth — payer needs more info"}[p["status"]]
        items.append({
            "id": f"pa:{p['id']}", "kind": "prior_auth",
            "title": label,
            "detail": f"{p.get('patient_name', '')} · {p.get('procedure_code') or 'no code'}",
            "severity": "medium", "link": f"/app/prior-auth/{p['id']}", "created_at": p["created_at"],
        })

    for a in _current_chain(appeals, "previous_appeal_id"):
        if a["status"] != "drafted":
            continue
        items.append({
            "id": f"appeal:{a['id']}", "kind": "appeal",
            "title": "Appeal drafted — review & send",
            "detail": f"{claim_by_id.get(a.get('claim_id'), {}).get('claim_id', 'denied claim')}",
            "severity": "medium",
            "link": f"/app/claims/{a['claim_id']}" if a.get("claim_id") else None,
            "created_at": a["created_at"],
        })

    _vr_action = {"skipped_no_consent", "skipped_no_phone", "reschedule_requested",
                  "wrong_person", "out_of_scope", "no_answer", "call_failed", "error"}
    for v in reminders:
        if v["status"] not in _vr_action:
            continue
        items.append({
            "id": f"vr:{v['id']}", "kind": "reminder",
            "title": f"Reminder call: {v['status'].replace('_', ' ')}",
            "detail": v.get("patient_name_snapshot", ""),
            "severity": "medium",
            "link": f"/app/appointments/{v['appointment_id']}" if v.get("appointment_id") else "/app/reminders",
            "created_at": v["created_at"],
        })

    items.sort(key=lambda i: i["created_at"], reverse=True)
    return {
        "organization_id": ctx.organization_id,
        "tasks": items,
        "counts": dict(Counter(i["kind"] for i in items)),
        "total": len(items),
    }


# --------------------------------------------------------------------------- #
# GET /api/payers — the payer directory + the rule config each agent checks
# --------------------------------------------------------------------------- #
@router.get("/api/payers")
async def payers(ctx: AuthContext = Depends(require_organization)) -> dict:
    t = ctx.access_token
    rows = await rest_get(t, "/payers", {"select": "*", "order": "name"})
    claims = await rest_get(t, "/claims", {"select": "payer_id,status,amount"})
    pas = await rest_get(t, "/prior_authorizations", {"select": "payer_id,status,previous_auth_id,id"})
    checks = await rest_get(t, "/eligibility_checks", {"select": "payer_id,status"})

    claims_by_payer: dict[str, Counter] = defaultdict(Counter)
    amt_by_payer: dict[str, float] = defaultdict(float)
    for c in claims:
        claims_by_payer[c.get("payer_id")][c.get("status")] += 1
        amt_by_payer[c.get("payer_id")] += float(c.get("amount") or 0)
    pa_cur = _current_chain(pas, "previous_auth_id")
    pa_by_payer: dict[str, Counter] = defaultdict(Counter)
    for p in pa_cur:
        pa_by_payer[p.get("payer_id")][p.get("status")] += 1
    chk_by_payer: dict[str, Counter] = defaultdict(Counter)
    for k in checks:
        chk_by_payer[k.get("payer_id")][k.get("status")] += 1

    out = []
    for p in rows:
        cc = claims_by_payer.get(p["id"], Counter())
        out.append({
            "id": p["id"], "name": p["name"],
            "rules": {
                "authorization_required": p.get("authorization_required"),
                "documentation_required": p.get("documentation_required"),
                "follow_up_threshold_days": p.get("follow_up_threshold_days"),
                "eligibility_verification_supported": p.get("eligibility_verification_supported"),
                "eligibility_active_threshold": p.get("eligibility_active_threshold"),
                "prior_auth_supported": p.get("prior_auth_supported"),
                "prior_auth_required_default": p.get("prior_auth_required_default"),
                "prior_auth_approval_threshold": p.get("prior_auth_approval_threshold"),
            },
            "claims": {"total": sum(cc.values()), "denied": cc.get("denied", 0),
                       "paid": cc.get("paid", 0), "billed_amount": round(amt_by_payer.get(p["id"], 0.0), 2)},
            "prior_auth": {"total": sum(pa_by_payer.get(p["id"], Counter()).values()),
                           "denied": pa_by_payer.get(p["id"], Counter()).get("auth_denied", 0),
                           "approved": pa_by_payer.get(p["id"], Counter()).get("auth_approved", 0)},
            "eligibility": {"total": sum(chk_by_payer.get(p["id"], Counter()).values()),
                            "inactive": chk_by_payer.get(p["id"], Counter()).get("verified_inactive", 0),
                            "failed": chk_by_payer.get(p["id"], Counter()).get("check_failed", 0)},
        })
    return {"organization_id": ctx.organization_id, "payers": out}


# --------------------------------------------------------------------------- #
# GET /api/patients — a unified record, soft-keyed by name (+ dob), aggregated
# from appointments / claims / coverages / eligibility / reminders
# --------------------------------------------------------------------------- #
@router.get("/api/patients")
async def patients(ctx: AuthContext = Depends(require_organization)) -> dict:
    t = ctx.access_token
    appts = await rest_get(t, "/appointments",
                           {"select": "id,patient_name,patient_dob,patient_member_id,payer_id,scheduled_at,is_emergency,created_at"})
    claims = await rest_get(t, "/claims",
                            {"select": "id,claim_id,patient_name,patient_member_id,status,risk_level,amount,created_at"})
    covs = await rest_get(t, "/patient_coverages",
                          {"select": "patient_name,patient_dob,payer_name,coverage_type,plan_kind"})
    checks = await rest_get(t, "/eligibility_checks",
                            {"select": "patient_name,status,created_at"})
    reminders = await rest_get(t, "/voice_reminders",
                               {"select": "patient_name_snapshot,status,created_at"})

    pmap: dict[str, dict] = {}

    def key(name: str | None) -> str:
        return (name or "").strip().lower()

    def bucket(name: str | None, dob: str | None = None) -> dict:
        k = key(name)
        if k not in pmap:
            pmap[k] = {"name": (name or "").strip(), "dob": dob,
                       "member_ids": set(), "payers": set(),
                       "appointments": 0, "claims": 0, "denied_claims": 0,
                       "high_risk_claims": 0, "billed_amount": 0.0,
                       "eligibility_issues": 0, "reminder_issues": 0,
                       "last_activity": None, "flags": set()}
        if dob and not pmap[k]["dob"]:
            pmap[k]["dob"] = dob
        return pmap[k]

    def touch(b: dict, ts: str | None):
        if ts and (b["last_activity"] is None or ts > b["last_activity"]):
            b["last_activity"] = ts

    for a in appts:
        b = bucket(a["patient_name"], a.get("patient_dob"))
        b["appointments"] += 1
        if a.get("patient_member_id"):
            b["member_ids"].add(a["patient_member_id"])
        if a.get("is_emergency"):
            b["flags"].add("emergency visit")
        touch(b, a.get("scheduled_at") or a.get("created_at"))
    for c in claims:
        b = bucket(c["patient_name"])
        b["claims"] += 1
        b["billed_amount"] += float(c.get("amount") or 0)
        if c.get("patient_member_id"):
            b["member_ids"].add(c["patient_member_id"])
        if c.get("status") == "denied":
            b["denied_claims"] += 1
            b["flags"].add("denied claim")
        if c.get("risk_level") == "High":
            b["high_risk_claims"] += 1
            b["flags"].add("high-risk claim")
        touch(b, c.get("created_at"))
    for cov in covs:
        b = bucket(cov["patient_name"], cov.get("patient_dob"))
        if cov.get("payer_name"):
            b["payers"].add(cov["payer_name"])
        if cov.get("coverage_type"):
            b["flags"].add(f"{cov['coverage_type']} coverage")
    for k in checks:
        b = bucket(k["patient_name"])
        if k.get("status") in ("verified_inactive", "check_failed", "insufficient_info"):
            b["eligibility_issues"] += 1
            b["flags"].add("eligibility needs review")
        touch(b, k.get("created_at"))
    for v in reminders:
        b = bucket(v["patient_name_snapshot"])
        if v.get("status") not in ("pending", "dispatching", "calling", "confirmed", "cancelled"):
            b["reminder_issues"] += 1
        touch(b, v.get("created_at"))

    out = []
    for b in pmap.values():
        if not b["name"]:
            continue
        out.append({**b,
                    "member_ids": sorted(b["member_ids"]),
                    "payers": sorted(b["payers"]),
                    "flags": sorted(b["flags"]),
                    "billed_amount": round(b["billed_amount"], 2)})
    out.sort(key=lambda p: (p["last_activity"] or ""), reverse=True)
    return {"organization_id": ctx.organization_id, "patients": out, "total": len(out)}


# --------------------------------------------------------------------------- #
# GET /api/reports — revenue-cycle KPIs + automation impact over the whole book
# --------------------------------------------------------------------------- #
@router.get("/api/reports")
async def reports(ctx: AuthContext = Depends(require_organization)) -> dict:
    t = ctx.access_token
    claims = await rest_get(t, "/claims", {"select": "status,risk_level,amount,created_at"})
    activity = await rest_get(t, "/activity_log", {"select": "actor,action"})
    pas = await rest_get(t, "/prior_authorizations", {"select": "id,status,previous_auth_id"})
    appeals = await rest_get(t, "/appeals", {"select": "id,status,previous_appeal_id,resolution_payload"})
    checks = await rest_get(t, "/eligibility_checks", {"select": "status"})
    reminders = await rest_get(t, "/voice_reminders", {"select": "status,outcome"})

    cs = Counter(c.get("status") for c in claims)
    cr = Counter(c.get("risk_level") for c in claims if c.get("risk_level"))
    denied_amt = sum(float(c.get("amount") or 0) for c in claims if c.get("status") == "denied")
    paid_amt = sum(float(c.get("amount") or 0) for c in claims if c.get("status") == "paid")
    total_amt = sum(float(c.get("amount") or 0) for c in claims)

    # automation impact: what agents executed vs. what they handed to a human
    agent_executed = sum(1 for a in activity if a.get("action") in ("executed", "response", "resolution"))
    escalated = sum(1 for a in activity if a.get("action") == "escalated")
    approvals = sum(1 for a in activity if a.get("action") == "human.approved")

    pa_cur = _current_chain(pas, "previous_auth_id")
    pcs = Counter(p.get("status") for p in pa_cur)
    submitted = pcs.get("auth_approved", 0) + pcs.get("auth_denied", 0) + pcs.get("info_needed", 0)

    ap_cur = _current_chain(appeals, "previous_appeal_id")
    aps = Counter(a.get("status") for a in ap_cur)
    appeals_resolved = aps.get("appeal_approved", 0) + aps.get("appeal_partial", 0) + aps.get("appeal_denied", 0)

    vr = Counter(v.get("status") for v in reminders)
    vr_completed = sum(v for k, v in vr.items() if k not in ("pending", "dispatching", "calling"))

    scored = cr.get("Low", 0) + cr.get("Medium", 0) + cr.get("High", 0)

    return {
        "organization_id": ctx.organization_id,
        "claims": {
            "total": len(claims),
            "by_status": dict(cs),
            "clean_rate_pct": round(100 * cr.get("Low", 0) / scored) if scored else None,
            "denial_rate_pct": round(100 * cs.get("denied", 0) / len(claims)) if claims else None,
            "billed_amount": round(total_amt, 2),
            "paid_amount": round(paid_amt, 2),
            "denied_amount": round(denied_amt, 2),
        },
        "automation": {
            "agent_actions_executed": agent_executed,
            "escalated_to_human": escalated,
            "human_approvals": approvals,
            "automation_rate_pct": round(100 * agent_executed / (agent_executed + escalated))
            if (agent_executed + escalated) else None,
        },
        "prior_auth": {
            "submitted": submitted,
            "approval_rate_pct": round(100 * pcs.get("auth_approved", 0) / submitted) if submitted else None,
            "not_required": pcs.get("not_required", 0),
            "emergency_exempt": pcs.get("emergency_exempt", 0),
        },
        "appeals": {
            "resolved": appeals_resolved,
            "win_rate_pct": round(100 * aps.get("appeal_approved", 0) / appeals_resolved) if appeals_resolved else None,
            "partial": aps.get("appeal_partial", 0),
            "upheld": aps.get("appeal_denied", 0),
        },
        "eligibility": dict(Counter(c.get("status") for c in checks)),
        "voice_reminders": {
            "completed": vr_completed,
            "confirmed": vr.get("confirmed", 0),
            "confirm_rate_pct": round(100 * vr.get("confirmed", 0) / vr_completed) if vr_completed else None,
            "by_status": dict(vr),
        },
    }
