#!/usr/bin/env python3
"""Phase 6 voice-reminder lifecycle — end-to-end against the local stack.

Exercises the n8n-facing automation endpoints exactly as the workflows call
them, with SYNTHETIC data only (a dedicated test clinic, +1-555-01xx numbers).
Vapi is never called: the test stands in for n8n + Vapi and supplies its own
call ids, then checks every state transition in Postgres.

Prereqs: local Supabase up (npx supabase start), migrations applied, and the
backend running against it, e.g.

    cd backend && uvicorn app.main:app --port 8001
    FORESIGHT_API=http://127.0.0.1:8001 python tests/e2e_voice_reminder_test.py

/due is the cross-clinic dispatch queue, so this test REFUSES to run if any
other clinic has a pending reminder that is already due — it would lease it.
"""
from __future__ import annotations

import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from _agentlib import BACKEND, _dotenv, _http, ensure_org, svc_get, svc_write, REST, ANON

API = os.environ.get("FORESIGHT_API", "http://127.0.0.1:8001").rstrip("/")
BASE = f"{API}/api/automation/voice-reminders"
TOKEN = {**_dotenv(BACKEND / ".env"), **os.environ}.get("N8N_SERVICE_TOKEN", "")
EMAIL = "voicetest-admin@foresight.test"
CLINIC = "Voice Reminder Test Clinic"

PASSED: list[str] = []


def check(name: str, cond: bool, detail=None) -> None:
    if not cond:
        sys.exit(f"FAIL: {name}" + (f" — {detail}" if detail is not None else ""))
    PASSED.append(name)
    print(f"  ok  {name}")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def now() -> datetime:
    return datetime.now(timezone.utc)


def auto(method: str, path: str, body=None, token: str | None = None):
    hdrs = {"Authorization": f"Bearer {TOKEN if token is None else token}"}
    return _http(method, f"{BASE}{path}", hdrs, body)


def reminder(vr_id: str) -> dict:
    return svc_get("voice_reminders", {"id": f"eq.{vr_id}", "select": "*"})[0]


def escalations_for(vr_id: str) -> list[dict]:
    return svc_get("escalations", {"voice_reminder_id": f"eq.{vr_id}", "select": "reason_code,context"})


def seed(org_id: str, uid: str, label: str, phone: str) -> tuple[str, str]:
    """One appointment + contact + GRANTED consent + a due pending reminder."""
    appt_at = now() + timedelta(hours=26)
    appt = svc_write("POST", "appointments", {}, {
        "organization_id": org_id, "patient_name": f"Test Patient {label}",
        "patient_member_id": f"TEST-{label}", "scheduled_at": iso(appt_at),
        "is_emergency": False, "patient_phone": phone,
    })[0]
    contact = svc_write("POST", "patient_contacts", {}, {
        "organization_id": org_id, "patient_name": f"Test Patient {label}", "phone": phone,
    })[0]
    svc_write("POST", "patient_consents", {}, {
        "organization_id": org_id, "patient_contact_id": contact["id"], "channel": "voice",
        "state": "granted", "source": "intake_form", "recorded_by": uid,
        "recorded_at": iso(now() - timedelta(minutes=5)),
    })
    vr = svc_write("POST", "voice_reminders", {}, {
        "organization_id": org_id, "appointment_id": appt["id"], "patient_contact_id": contact["id"],
        "patient_name_snapshot": f"Test Patient {label}", "patient_phone_snapshot": phone,
        "clinic_name_snapshot": CLINIC, "appointment_at_snapshot": iso(appt_at),
        "timezone_snapshot": "America/New_York", "consent_snapshot": True,
        "status": "pending", "scheduled_call_at": iso(now() - timedelta(minutes=1)),
        "authorized_by": uid, "authorized_at": iso(now()),
    })[0]
    return vr["id"], contact["id"]


def revoke(org_id: str, uid: str, contact_id: str) -> None:
    svc_write("POST", "patient_consents", {}, {
        "organization_id": org_id, "patient_contact_id": contact_id, "channel": "voice",
        "state": "revoked", "source": "patient_request", "recorded_by": uid,
    })


def lease_all() -> list[str]:
    st, body = auto("GET", "/due")
    check("/due responds 200", st == 200, (st, body))
    return [d["voice_reminder_id"] for d in body["due"]]


def report(vr_call_id: str, outcome: str | None = "confirmed") -> dict:
    msg = {"type": "end-of-call-report", "call": {"id": vr_call_id}, "endedReason": "assistant-ended-call"}
    if outcome:
        msg["analysis"] = {"structuredData": {"outcome": outcome}}
    return msg


def main() -> None:
    if not TOKEN:
        sys.exit("N8N_SERVICE_TOKEN is not set in backend/.env — cannot authenticate as n8n")
    st, _ = _http("GET", f"{API}/health")
    if st != 200:
        sys.exit(f"backend not reachable at {API} (status {st}) — start it first")

    org_id, uid = ensure_org(EMAIL, CLINIC)
    svc_write("DELETE", "voice_reminders", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "patient_consents", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "patient_contacts", {"organization_id": f"eq.{org_id}"}, None)

    foreign_due = svc_get("voice_reminders", {
        "status": "in.(pending,dispatching)", "organization_id": f"neq.{org_id}",
        "scheduled_call_at": f"lte.{iso(now())}", "select": "id",
    })
    if foreign_due:
        sys.exit(f"refusing to run: {len(foreign_due)} other-clinic reminder(s) are due — /due would lease them")

    print("auth")
    st, _ = auto("GET", "/due", token="")
    check("no token -> 401/403", st in (401, 403), st)
    st, _ = auto("GET", "/due", token="wrong-token")
    check("wrong token -> 401", st == 401, st)
    st, body = _http("POST", f"{REST}/rpc/voice_authorize_dial",
                     {"apikey": ANON, "Authorization": f"Bearer {ANON}"},
                     {"p_reminder_id": str(uuid.uuid4()), "p_lease_cutoff": iso(now())})
    check("anon cannot execute voice_authorize_dial", st in (401, 403, 404), (st, body))

    # ---------------------------------------------------------------- Test A
    print("A — consent granted: full lifecycle, one call id end to end")
    a_id, _ = seed(org_id, uid, "A", "+15550100001")
    leased = lease_all()
    check("A leased by /due", a_id in leased)
    check("A status dispatching", reminder(a_id)["status"] == "dispatching")
    st, body = auto("POST", f"/{a_id}/authorize-dial")
    check("A authorize-dial -> authorized", st == 200 and body["authorized"] is True, body)
    st, body = auto("POST", f"/{a_id}/authorize-dial")
    check("A second authorize-dial refused (single-use)",
          st == 200 and body["authorized"] is False and body["reason"] == "already_authorized", body)
    call_a = f"test-call-{uuid.uuid4()}"
    st, body = auto("POST", f"/{a_id}/mark-calling", {"vapi_call_id": call_a, "vapi_call_status": "queued"})
    check("A mark-calling -> calling", st == 200 and body["status"] == "calling", (st, body))
    row = reminder(a_id)
    check("A stored vapi_call_id === Vapi call id (exact)", row["vapi_call_id"] == call_a, row["vapi_call_id"])
    check("A vapi_call_status stored", row["outcome_payload"].get("vapi_call_status") == "queued", row["outcome_payload"])
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_a, "message": report(call_a)})
    check("A outcome matched + confirmed", st == 200 and body["status"] == "confirmed", (st, body))
    check("A DB status confirmed", reminder(a_id)["status"] == "confirmed")
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_a, "message": report(call_a, "wrong_person")})
    check("A replayed outcome is idempotent (terminal not rewritten)",
          st == 200 and body.get("idempotent") is True and reminder(a_id)["status"] == "confirmed", body)

    # ---------------------------------------------------------------- Test B
    print("B1 — consent revoked after /due, before dial: never authorized")
    b1_id, b1_contact = seed(org_id, uid, "B1", "+15550100002")
    check("B1 leased", b1_id in lease_all())
    revoke(org_id, uid, b1_contact)
    st, body = auto("POST", f"/{b1_id}/authorize-dial")
    check("B1 authorize-dial refused: consent_revoked",
          st == 200 and body["authorized"] is False and body["reason"] == "consent_revoked", body)
    row = reminder(b1_id)
    check("B1 status skipped_no_consent", row["status"] == "skipped_no_consent", row["status"])
    check("B1 never dial-authorized", row["dial_authorized_at"] is None)
    check("B1 escalated", any(e["reason_code"] == "voice_reminder_no_consent_needs_human"
                              for e in escalations_for(b1_id)))

    print("B2 — consent revoked after authorize, before mark-calling: call refused, not stored")
    b2_id, b2_contact = seed(org_id, uid, "B2", "+15550100003")
    check("B2 leased", b2_id in lease_all())
    st, body = auto("POST", f"/{b2_id}/authorize-dial")
    check("B2 authorized", body.get("authorized") is True, body)
    revoke(org_id, uid, b2_contact)
    call_b2 = f"test-call-{uuid.uuid4()}"
    st, body = auto("POST", f"/{b2_id}/mark-calling", {"vapi_call_id": call_b2})
    check("B2 mark-calling -> 409 consent_revoked (n8n ends call)",
          st == 409 and body["detail"]["reason"] == "consent_revoked", (st, body))
    row = reminder(b2_id)
    check("B2 status skipped_no_consent (not calling)", row["status"] == "skipped_no_consent", row["status"])
    check("B2 call id NOT bound to reminder", row["vapi_call_id"] is None)
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_b2, "message": report(call_b2)})
    check("B2 later outcome for ended call is ignored", body.get("ignored") is True, body)
    check("B2 status unchanged", reminder(b2_id)["status"] == "skipped_no_consent")

    print("B3 — a call reported without the pre-dial gate is never accepted")
    b3_id, _ = seed(org_id, uid, "B3", "+15550100004")
    check("B3 leased", b3_id in lease_all())
    st, body = auto("POST", f"/{b3_id}/mark-calling", {"vapi_call_id": f"test-call-{uuid.uuid4()}"})
    check("B3 mark-calling without authorize -> 409 not_dial_authorized",
          st == 409 and body["detail"]["reason"] == "not_dial_authorized", (st, body))
    check("B3 status error + escalated", reminder(b3_id)["status"] == "error" and any(
        e["reason_code"] == "voice_reminder_dialled_without_authorization" for e in escalations_for(b3_id)))

    # ---------------------------------------------------------------- Test C
    print("C — backend unreachable after dial: lease sweep escalates, nothing can revert it")
    c_id, _ = seed(org_id, uid, "C", "+15550100005")
    check("C leased", c_id in lease_all())
    st, body = auto("POST", f"/{c_id}/authorize-dial")
    check("C authorized", body.get("authorized") is True, body)
    # mark-calling never arrives (backend was down); age the lease past the cutoff
    svc_write("PATCH", "voice_reminders", {"id": f"eq.{c_id}"},
              {"dispatched_at": iso(now() - timedelta(hours=2))})
    st, body = auto("GET", "/due")
    check("C swept by /due", st == 200 and body["swept"] >= 1, body)
    row = reminder(c_id)
    esc = [e for e in escalations_for(c_id) if e["reason_code"] == "voice_reminder_dispatch_lost"]
    check("C status error", row["status"] == "error", row["status"])
    check("C escalation flags call_may_have_been_placed",
          len(esc) == 1 and esc[0]["context"].get("call_may_have_been_placed") is True, esc)
    st, body = auto("POST", f"/{c_id}/mark-calling", {"vapi_call_id": f"test-call-{uuid.uuid4()}"})
    check("C late mark-calling rejected (terminal cannot revert)", st == 409, (st, body))
    check("C still error", reminder(c_id)["status"] == "error")
    st, body = auto("GET", "/due")
    check("C not swept twice (one escalation)",
          len([e for e in escalations_for(c_id) if e["reason_code"] == "voice_reminder_dispatch_lost"]) == 1)

    # ---------------------------------------------------------------- Test D
    print("D — one Vapi failure does not affect the rest of the batch")
    d1_id, _ = seed(org_id, uid, "D1", "+15550100006")
    d2_id, _ = seed(org_id, uid, "D2", "+15550100007")
    leased = lease_all()
    check("D1 + D2 leased in one batch", d1_id in leased and d2_id in leased, leased)
    for vid in (d1_id, d2_id):
        auto("POST", f"/{vid}/authorize-dial")
    st, body = auto("POST", f"/{d1_id}/dispatch-failed",
                    {"error": "call create failed", "vapi_status": "400",
                     "detail": "Bad Request: customer.number invalid"})
    check("D1 dispatch-failed -> call_failed", st == 200 and body["status"] == "call_failed", (st, body))
    esc = [e for e in escalations_for(d1_id) if e["reason_code"] == "voice_reminder_call_failed"]
    check("D1 diagnostics preserved (real values, no '=' prefix)",
          len(esc) == 1 and esc[0]["context"]["vapi_status"] == "400"
          and esc[0]["context"]["detail"].startswith("Bad Request"), esc)
    call_d2 = f"test-call-{uuid.uuid4()}"
    st, body = auto("POST", f"/{d2_id}/mark-calling", {"vapi_call_id": call_d2})
    check("D2 unaffected: mark-calling -> calling", st == 200 and reminder(d2_id)["status"] == "calling", (st, body))
    st, body = auto("POST", f"/{d1_id}/dispatch-failed", {"error": "again"})
    check("D1 second dispatch-failed rejected (no second escalation)", st == 409, (st, body))

    # ---------------------------------------------------------------- Test E
    print("E — outcome id integrity")
    before = {vid: reminder(vid)["status"] for vid in (a_id, d2_id)}
    unknown = f"unknown-{uuid.uuid4()}"
    st, body = auto("POST", "/outcome", {"vapi_call_id": unknown, "message": report(unknown)})
    check("E unknown call id ignored", st == 200 and body.get("ignored") is True, body)
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_d2, "message": report(f"other-{uuid.uuid4()}")})
    check("E forwarded id != report call.id -> 422", st == 422, (st, body))
    st, body = auto("POST", "/outcome", {"message": report(call_d2)})
    check("E missing vapi_call_id -> 422", st == 422, (st, body))
    after = {vid: reminder(vid)["status"] for vid in (a_id, d2_id)}
    check("E no reminder changed by bad outcomes", before == after, (before, after))
    st, body = auto("POST", f"/{d2_id}/mark-calling", {"vapi_call_id": call_a})
    check("E call id already bound elsewhere cannot be rebound", st == 409, (st, body))
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_d2, "message": report(call_d2, None)})
    check("E D2 matched by its own id -> no_answer + escalated",
          body.get("status") == "no_answer" and body.get("escalated") is True, body)

    # ---------------------------------------------------------------- concurrency
    print("race — two overlapping /due polls lease a reminder at most once")
    r_id, _ = seed(org_id, uid, "R", "+15550100008")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auto("GET", "/due"), range(2)))
    hits = sum(1 for st, body in results if st == 200 and r_id in [d["voice_reminder_id"] for d in body["due"]])
    check("race: leased by exactly one poll", hits == 1, results)
    with ThreadPoolExecutor(max_workers=2) as pool:
        auths = list(pool.map(lambda _: auto("POST", f"/{r_id}/authorize-dial"), range(2)))
    check("race: concurrent authorize-dial authorizes exactly once",
          sum(1 for st, b in auths if b.get("authorized") is True) == 1, auths)

    print("F — non-final Vapi messages never close a live call; lost outcomes escalate")
    f_id, _ = seed(org_id, uid, "F", "+15550100009")
    check("F leased", f_id in lease_all())
    auto("POST", f"/{f_id}/authorize-dial")
    call_f = f"test-call-{uuid.uuid4()}"
    auto("POST", f"/{f_id}/mark-calling", {"vapi_call_id": call_f})
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_f,
                                         "message": {"type": "status-update", "status": "in-progress",
                                                     "call": {"id": call_f}}})
    check("F status-update ignored", body.get("ignored") is True
          and body.get("reason") == "not_end_of_call_report", body)
    check("F still calling", reminder(f_id)["status"] == "calling")
    svc_write("PATCH", "voice_reminders", {"id": f"eq.{f_id}"},
              {"placed_at": iso(now() - timedelta(hours=5))})
    auto("GET", "/due")
    check("F lost outcome swept to error + escalated",
          reminder(f_id)["status"] == "error" and any(
              e["reason_code"] == "voice_reminder_outcome_missing" for e in escalations_for(f_id)))
    st, body = auto("POST", "/outcome", {"vapi_call_id": call_f, "message": report(call_f)})
    check("F late outcome cannot revert swept reminder",
          body.get("idempotent") is True and reminder(f_id)["status"] == "error", body)

    print("end-call-failed")
    st, body = auto("POST", f"/{b2_id}/end-call-failed", {"vapi_call_id": call_b2, "error": "timeout"})
    check("end-call-failed always escalates", st == 200 and body["escalated"] is True, body)

    print(f"\nALL {len(PASSED)} CHECKS PASSED — voice-reminder lifecycle holds.")


if __name__ == "__main__":
    main()
