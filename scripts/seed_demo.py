#!/usr/bin/env python3
"""One fully-populated demo clinic, for showing the whole app at once.

Creates (idempotently) a single organization + admin user and fills every
section: payers, patients, appointments, eligibility checks, prior
authorizations, coverages (with COB), procedure prices, cost estimates, card
scans, claims (issues + reasoning + recommendations + follow-ups), appeals,
voice reminders (contacts + consent), escalations, and activity log.

Everything is inserted directly via the service-role REST — no orchestrator, no
LLM. The data is shaped to look like it went through the pipeline so the
Patients / Insurance / Tasks / Reports aggregate views populate.

    python scripts/seed_demo.py

Login afterward:  demo@foresight.test  /  Foresight-demo-2026!
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOW = datetime.now(timezone.utc)
ORG_NAME = "Northside Community Health"
EMAIL = "demo@foresight.test"
PW = "Foresight-demo-2026!"
TZ = "America/Chicago"


def _http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _dotenv(p):
    out = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def cfg():
    env = {**_dotenv(BACKEND / ".env"), **os.environ}
    c = {"api": env.get("SUPABASE_URL"), "anon": env.get("SUPABASE_ANON_KEY"),
         "svc": env.get("SUPABASE_SERVICE_ROLE_KEY")}
    if not all(c.values()):
        out = subprocess.run(["npx", "--yes", "supabase", "status", "-o", "json"],
                             capture_output=True, text=True, timeout=90,
                             shell=(os.name == "nt")).stdout
        s = {k.upper(): v for k, v in json.loads(out).items()}
        c["api"] = c["api"] or s.get("API_URL")
        c["anon"] = c["anon"] or s.get("ANON_KEY")
        c["svc"] = c["svc"] or s.get("SERVICE_ROLE_KEY")
    c["api"] = (c["api"] or "http://127.0.0.1:54321").rstrip("/")
    if not c["svc"]:
        sys.exit("no service_role key — is the local stack up?")
    return c


C = cfg()
REST = f"{C['api']}/rest/v1"
AUTH = f"{C['api']}/auth/v1"
SVC, ANON = C["svc"], C["anon"]


def A():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def sg(path, params):
    q = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in params.items())
    return _http("GET", f"{REST}/{path}?{q}", A())[1] or []


def ins(path, rows):
    """Insert row(s) one at a time (PostgREST bulk needs identical key sets)."""
    out = []
    for row in rows:
        st, resp = _http("POST", f"{REST}/{path}",
                         {**A(), "Prefer": "return=representation"}, row)
        if st >= 300:
            raise RuntimeError(f"POST {path} -> {st} {resp}")
        out.extend(resp if isinstance(resp, list) else [resp])
    return out


def delete_all(path, org_id):
    _http("DELETE", f"{REST}/{path}?organization_id=eq.{org_id}", A())


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
def ensure_org():
    users = (_http("GET", f"{AUTH}/admin/users?per_page=200", A())[1] or {}).get("users", [])
    by_email = {u["email"]: u["id"] for u in users}
    existing = sg("organizations", {"name": f"eq.{ORG_NAME}", "select": "id"})
    if existing:
        oid = existing[0]["id"]
        uid = by_email.get(EMAIL)
        if uid is None:
            uid = _http("POST", f"{AUTH}/admin/users", A(),
                        {"email": EMAIL, "password": PW, "email_confirm": True})[1]["id"]
            _http("PATCH", f"{REST}/profiles?id=eq.{uid}", {**A(), "Prefer": "return=representation"},
                  {"organization_id": oid, "role": "clinic_admin"})
        return oid, uid
    if EMAIL not in by_email:
        _http("POST", f"{AUTH}/admin/users", A(), {"email": EMAIL, "password": PW, "email_confirm": True})
    tok = _http("POST", f"{AUTH}/token?grant_type=password", {"apikey": ANON},
                {"email": EMAIL, "password": PW})[1]["access_token"]
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {tok}"}, {"org_name": ORG_NAME})
    if st != 200:
        sys.exit(f"bootstrap failed: {st} {body}")
    oid = body["id"]
    users = (_http("GET", f"{AUTH}/admin/users?per_page=200", A())[1] or {}).get("users", [])
    uid = {u["email"]: u["id"] for u in users}[EMAIL]
    return oid, uid


# --------------------------------------------------------------------------- #
PAYERS = [
    ("Meridian Health Plan", dict(authorization_required=False, documentation_required=True,
        follow_up_threshold_days=21, eligibility_verification_supported=True, eligibility_active_threshold=90,
        prior_auth_supported=True, prior_auth_required_default=False, prior_auth_approval_threshold=88)),
    ("BlueRidge PPO", dict(authorization_required=True, documentation_required=False,
        follow_up_threshold_days=30, eligibility_verification_supported=True, eligibility_active_threshold=82,
        prior_auth_supported=True, prior_auth_required_default=True, prior_auth_approval_threshold=72)),
    ("Cascade Medicaid", dict(authorization_required=False, documentation_required=True,
        follow_up_threshold_days=14, eligibility_verification_supported=True, eligibility_active_threshold=68,
        prior_auth_supported=True, prior_auth_required_default=True, prior_auth_approval_threshold=60)),
    ("Summit Commercial", dict(authorization_required=True, documentation_required=True,
        follow_up_threshold_days=None, eligibility_verification_supported=False, eligibility_active_threshold=0,
        prior_auth_supported=False, prior_auth_required_default=True, prior_auth_approval_threshold=78)),
    ("Harbor Medicare Advantage", dict(authorization_required=False, documentation_required=False,
        follow_up_threshold_days=45, eligibility_verification_supported=True, eligibility_active_threshold=95,
        prior_auth_supported=True, prior_auth_required_default=False, prior_auth_approval_threshold=85)),
]

# name, dob, member_id, payer index
PATIENTS = [
    ("Maria Alvarez", "1988-03-14", "MHP-4471820", 0),
    ("James Okafor", "1975-11-02", "BRP-9930012", 1),
    ("Priya Nair", "1992-07-21", "CM-7761145", 2),
    ("Daniel Cho", "1969-01-30", "SC-2200781", 3),
    ("Sofia Rossi", "2001-05-09", "MHP-5540991", 0),
    ("Marcus Bell", "1983-09-17", "HMA-1120345", 4),
    ("Aisha Rahman", "1995-12-25", "BRP-6650228", 1),
    ("Ethan Brooks", "1958-06-03", "HMA-3390567", 4),
    ("Lucia Mendez", "1979-08-11", "CM-8812390", 2),
    ("Noah Fischer", "2010-02-19", "MHP-7729004", 0),
]

PROCEDURES = [
    ("99213", "Office visit, established patient, low complexity", 145.00),
    ("99214", "Office visit, established patient, moderate complexity", 210.00),
    ("72148", "MRI lumbar spine without contrast", 1180.00),
    ("70553", "MRI brain with and without contrast", 1650.00),
    ("29881", "Knee arthroscopy with meniscectomy", 3400.00),
    ("95810", "Polysomnography / sleep study", 1250.00),
    ("80053", "Comprehensive metabolic panel", 47.00),
    ("93000", "Electrocardiogram, complete", 65.00),
]


def main():
    oid, uid = ensure_org()
    _http("PATCH", f"{REST}/organizations?id=eq.{oid}",
          {**A(), "Prefer": "return=representation"}, {"timezone": TZ})

    # wipe prior demo data (order matters for FKs)
    for tbl in ("escalations", "activity_log", "follow_ups", "recommendations", "claim_issues",
                "appeals", "voice_reminders", "patient_consents", "patient_contacts",
                "cost_estimates", "card_scans", "patient_coverages", "prior_authorizations",
                "eligibility_checks", "claims", "appointments", "procedure_prices", "payers"):
        delete_all(tbl, oid)

    # --- payers ---
    payers = ins("payers", [{"organization_id": oid, "name": n, **k} for n, k in PAYERS])
    pid = [p["id"] for p in payers]

    # --- procedure prices ---
    ins("procedure_prices", [{"organization_id": oid, "procedure_code": c, "description": d,
                              "base_price": p} for c, d, p in PROCEDURES])

    # --- appointments (past + upcoming) ---
    appts = []
    for i, (name, dob, mid, px) in enumerate(PATIENTS):
        offset_h = [-72, -36, 20, 30, 44, -8, 52, 76, -120, 26][i]
        appts.append({
            "organization_id": oid, "patient_name": name, "patient_member_id": mid,
            "patient_dob": dob, "payer_id": pid[px],
            "scheduled_at": iso(NOW + timedelta(hours=offset_h)),
            "is_emergency": i == 5,
        })
    appts = ins("appointments", appts)
    ap = {PATIENTS[i][0]: appts[i]["id"] for i in range(len(PATIENTS))}

    # --- eligibility checks ---
    elig_map = {
        "Maria Alvarez": ("verified_active", 90), "James Okafor": ("verified_active", 88),
        "Priya Nair": ("verified_inactive", 70), "Daniel Cho": ("check_failed", 0),
        "Sofia Rossi": ("verified_active", 92), "Marcus Bell": ("insufficient_info", 0),
        "Aisha Rahman": ("verified_active", 84), "Ethan Brooks": ("verified_active", 96),
        "Lucia Mendez": ("pending", 0), "Noah Fischer": ("verified_active", 91),
    }
    er = []
    for i, (name, dob, mid, px) in enumerate(PATIENTS):
        stt, bkt = elig_map[name]
        payload = {"simulated": True, "status": stt, "member_bucket": bkt,
                   "payer": PAYERS[px][0], "recheck_recommended": stt in ("insufficient_info", "check_failed")}
        er.append({
            "organization_id": oid, "appointment_id": ap[name], "patient_name": name,
            "patient_member_id": mid if stt != "insufficient_info" else "",
            "payer_id": pid[px], "payer_name": PAYERS[px][0], "is_emergency": i == 5,
            "status": stt, "result_payload": payload,
            "checked_at": None if stt == "pending" else iso(NOW - timedelta(hours=6)),
        })
    ins("eligibility_checks", er)

    # --- prior authorizations ---
    pa_specs = [
        ("James Okafor", 1, "72148", "MRI lumbar spine without contrast", "outpatient", "required_draft"),
        ("Aisha Rahman", 1, "29881", "Knee arthroscopy with meniscectomy", "outpatient", "auth_approved"),
        ("Priya Nair", 2, "95810", "Polysomnography / sleep study", "outpatient", "auth_denied"),
        ("Lucia Mendez", 2, "70553", "MRI brain with and without contrast", "outpatient", "info_needed"),
        ("Marcus Bell", 4, "99214", "Office visit, moderate complexity", "emergency", "emergency_exempt"),
        ("Sofia Rossi", 0, "99213", "Office visit, low complexity", "office", "not_required"),
    ]
    pas = []
    for name, px, code, desc, pos, stt in pa_specs:
        det = {"simulated": True, "determination": stt, "payer": PAYERS[px][0],
               "procedure_code": code, "required": stt in ("required_draft", "auth_approved", "auth_denied", "info_needed"),
               "matched_rule": "ALWAYS_AUTH_PROCEDURES" if code in ("72148", "70553", "29881") else "payer_default"}
        req = {"simulated": True, "channel": "electronic", "procedure_code": code,
               "clinical_justification": "Conservative therapy without improvement; imaging/procedure indicated per payer policy."} \
            if stt in ("required_draft", "auth_approved", "auth_denied", "info_needed") else {}
        resp = {}
        auth_no = None
        submitted = resolved = None
        if stt == "auth_approved":
            resp = {"response_status": "approved", "bucket": 31, "reason": "meets medical-necessity criteria"}
            auth_no = f"AUTH-2026-{code}-0031"
            submitted = iso(NOW - timedelta(days=3)); resolved = iso(NOW - timedelta(days=2))
        elif stt == "auth_denied":
            resp = {"response_status": "denied", "bucket": 91, "reason": "insufficient documentation of conservative treatment"}
            submitted = iso(NOW - timedelta(days=4)); resolved = iso(NOW - timedelta(days=2))
        elif stt == "info_needed":
            resp = {"response_status": "info_needed", "bucket": 77, "reason": "additional clinical notes required"}
            submitted = iso(NOW - timedelta(days=2)); resolved = iso(NOW - timedelta(days=1))
        pas.append({
            "organization_id": oid, "appointment_id": ap[name], "patient_name": name,
            "patient_member_id": next(p[2] for p in PATIENTS if p[0] == name),
            "payer_id": pid[px], "payer_name": PAYERS[px][0], "procedure_code": code,
            "procedure_description": desc, "place_of_service": pos, "is_emergency": pos == "emergency",
            "status": stt, "determination_payload": det, "request_payload": req,
            "response_payload": resp, "authorization_number": auth_no,
            "determined_at": iso(NOW - timedelta(days=5)),
            "submitted_at": submitted, "resolved_at": resolved,
        })
    ins("prior_authorizations", pas)

    # --- patient coverages (incl. COB: Noah has parent primary + own secondary) ---
    covs = [
        dict(patient_name="Maria Alvarez", patient_dob="1988-03-14", payer_id=pid[0],
             payer_name="Meridian Health Plan", member_id="MHP-4471820", plan_kind="medical",
             coverage_type="employer_active", relationship_to_subscriber="self",
             subscriber_name="Maria Alvarez", subscriber_dob="1988-03-14", effective_date="2026-01-01"),
        dict(patient_name="James Okafor", patient_dob="1975-11-02", payer_id=pid[1],
             payer_name="BlueRidge PPO", member_id="BRP-9930012", plan_kind="medical",
             coverage_type="employer_active", relationship_to_subscriber="self",
             subscriber_name="James Okafor", subscriber_dob="1975-11-02", effective_date="2025-07-01"),
        dict(patient_name="James Okafor", patient_dob="1975-11-02", payer_id=pid[4],
             payer_name="Harbor Medicare Advantage", member_id="HMA-JJ-22", plan_kind="medical",
             coverage_type="medicare", relationship_to_subscriber="self",
             subscriber_name="James Okafor", subscriber_dob="1975-11-02", effective_date="2026-01-01"),
        dict(patient_name="Ethan Brooks", patient_dob="1958-06-03", payer_id=pid[4],
             payer_name="Harbor Medicare Advantage", member_id="HMA-3390567", plan_kind="medical",
             coverage_type="medicare", relationship_to_subscriber="self",
             subscriber_name="Ethan Brooks", subscriber_dob="1958-06-03", effective_date="2024-01-01"),
        dict(patient_name="Noah Fischer", patient_dob="2010-02-19", payer_id=pid[0],
             payer_name="Meridian Health Plan", member_id="MHP-DAD-01", plan_kind="medical",
             coverage_type="employer_active", relationship_to_subscriber="child", is_dependent=True,
             subscriber_name="Karl Fischer", subscriber_dob="1980-04-10", effective_date="2026-01-01"),
        dict(patient_name="Noah Fischer", patient_dob="2010-02-19", payer_id=pid[1],
             payer_name="BlueRidge PPO", member_id="BRP-MOM-02", plan_kind="medical",
             coverage_type="employer_active", relationship_to_subscriber="child", is_dependent=True,
             subscriber_name="Lena Fischer", subscriber_dob="1982-09-02", effective_date="2026-01-01"),
        dict(patient_name="Aisha Rahman", patient_dob="1995-12-25", payer_id=pid[1],
             payer_name="BlueRidge PPO", member_id="BRP-6650228", plan_kind="medical",
             coverage_type="individual", relationship_to_subscriber="self",
             subscriber_name="Aisha Rahman", subscriber_dob="1995-12-25", effective_date="2026-02-01"),
        dict(patient_name="Lucia Mendez", patient_dob="1979-08-11", payer_id=pid[2],
             payer_name="Cascade Medicaid", member_id="CM-8812390", plan_kind="medical",
             coverage_type="medicaid", relationship_to_subscriber="self",
             subscriber_name="Lucia Mendez", subscriber_dob="1979-08-11", effective_date="2025-01-01"),
    ]
    ins("patient_coverages", [{"organization_id": oid, "group_number": "GRP-2200", **c} for c in covs])

    # --- cost estimates (self-pay GFE) ---
    disclaimer = ("This Good Faith Estimate shows the expected cost of care. It is not a bill. "
                  "Actual charges may differ. You have the right to dispute a bill that is $400 or "
                  "more above this estimate. (No Surprises Act)")
    for name, codes in [("Sofia Rossi", ["99213", "80053"]),
                        ("Daniel Cho", ["72148"]),
                        ("Marcus Bell", ["99214", "93000"])]:
        items = [{"procedure_code": c, "description": d, "base_price": p}
                 for c, d, p in PROCEDURES if c in codes]
        sub = round(sum(x["base_price"] for x in items), 2)
        ins("cost_estimates", [{
            "organization_id": oid, "appointment_id": ap[name], "patient_name": name,
            "patient_dob": next(p[1] for p in PATIENTS if p[0] == name),
            "line_items": items, "subtotal": sub, "currency": "USD",
            "patient_summary": f"Your visit is estimated at ${sub:.2f} before any discount. "
                               f"This covers {len(items)} service(s).",
            "disclaimer_text": disclaimer, "disclaimer_version": "nsa-gfe-2026-01",
            "self_pay_confirmed": True, "generated_by": uid,
        }])

    # --- card scans ---
    ins("card_scans", [
        {"organization_id": oid, "appointment_id": ap["Aisha Rahman"], "patient_name": "Aisha Rahman",
         "image_path": f"{oid}/demo/card-aisha.jpg", "image_mime": "image/jpeg", "status": "needs_review",
         "model": "claude-opus-5",
         "extracted_fields": {"member_id": "BRP-6650228", "group_number": "GRP-2200",
                              "payer_name": "BlueRidge PPO", "plan_type": None},
         "field_confidence": {"member_id": {"confidence": "high", "legible": True},
                              "group_number": {"confidence": "medium", "legible": True},
                              "payer_name": {"confidence": "high", "legible": True},
                              "plan_type": {"confidence": "low", "legible": False}}},
        {"organization_id": oid, "appointment_id": ap["Sofia Rossi"], "patient_name": "Sofia Rossi",
         "image_path": f"{oid}/demo/card-sofia.jpg", "image_mime": "image/jpeg", "status": "confirmed",
         "model": "claude-opus-5", "applied_to_appointment": True, "reviewed_by": uid,
         "reviewed_at": iso(NOW - timedelta(days=1)),
         "image_retain_until": iso(NOW + timedelta(days=30)),
         "extracted_fields": {"member_id": "MHP-5540991", "group_number": "GRP-2200",
                              "payer_name": "Meridian Health Plan", "plan_type": "PPO"},
         "field_confidence": {k: {"confidence": "high", "legible": True}
                              for k in ("member_id", "group_number", "payer_name", "plan_type")}},
    ])

    # --- claims ---
    claim_specs = [
        # patient, payer idx, amount, status, risk, issues[(type,sev)], auth, doc, coding, denial_reason
        ("Maria Alvarez", 0, 210.00, "paid", "Low", [], True, True, True, None),
        ("Sofia Rossi", 0, 145.00, "cleared", "Low", [], True, True, True, None),
        ("Ethan Brooks", 4, 65.00, "actioned", "Low", [("overdue_follow_up", "low")], True, True, True, None),
        ("James Okafor", 1, 1180.00, "awaiting_approval", "High",
         [("missing_authorization", "high")], False, True, True, None),
        ("Aisha Rahman", 1, 3400.00, "reasoned", "High",
         [("missing_authorization", "high"), ("missing_documentation", "high")], False, False, True, None),
        ("Lucia Mendez", 2, 47.00, "analyzed", "Low", [("code_mismatch", "medium")], True, True, False, None),
        ("Priya Nair", 2, 1250.00, "denied", "High",
         [("missing_authorization", "high")], False, True, True,
         "Prior authorization was required for 95810 and was not obtained."),
        ("Daniel Cho", 3, 1180.00, "denied", "Medium",
         [("missing_documentation", "high")], True, False, True,
         "Medical records did not support medical necessity for the imaging study."),
        ("Marcus Bell", 4, 275.00, "awaiting_approval", "Medium",
         [("code_mismatch", "medium")], True, True, False, None),
        ("Noah Fischer", 0, 145.00, "received", "Low", [], True, True, True, None),
    ]
    claims = []
    for i, (name, px, amt, stt, risk, issues, auth, doc, coding, denial) in enumerate(claim_specs):
        rscore = {"Low": 20, "Medium": 45, "High": 85}[risk]
        summary = None
        detail = None
        if stt in ("reasoned", "awaiting_approval", "denied", "actioned"):
            summary = ("This claim has issues that commonly cause denials. "
                       + ("Prior authorization is missing. " if any(t == "missing_authorization" for t, _ in issues) else "")
                       + ("Supporting documentation is missing. " if any(t == "missing_documentation" for t, _ in issues) else "")
                       + ("The billed code does not match the documented service. " if any(t == "code_mismatch" for t, _ in issues) else "")).strip()
            detail = [{"issue_type": t, "explanation": f"{t.replace('_', ' ').capitalize()} — resolve before submission."} for t, _ in issues]
        claims.append({
            "organization_id": oid, "claim_id": f"CLM-2026-{1000 + i}", "payer_id": pid[px],
            "patient_name": name, "patient_member_id": next(p[2] for p in PATIENTS if p[0] == name),
            "amount": amt, "status": stt, "risk_score": rscore, "risk_level": risk,
            "authorization_present": auth, "documentation_present": doc, "coding_matches": coding,
            "last_followup_at": iso(NOW - timedelta(days=40)) if any(t == "overdue_follow_up" for t, _ in issues) else None,
            "denial_reason": denial, "reasoning_summary": summary, "reasoning_detail": detail,
            "reasoning_generated_at": iso(NOW - timedelta(days=1)) if summary else None,
        })
    claims = ins("claims", claims)
    cby = {claims[i]["claim_id"]: claims[i] for i in range(len(claims))}
    claim_of_patient = {claim_specs[i][0]: claims[i] for i in range(len(claim_specs))}

    # claim_issues + recommendations + follow_ups
    issue_desc = {
        "missing_authorization": "Payer requires prior authorization for this service; none is on file.",
        "missing_documentation": "Required supporting documentation is not attached.",
        "code_mismatch": "The billed procedure code does not match the documented encounter.",
        "overdue_follow_up": "No payer follow-up in longer than this payer's threshold.",
    }
    rec_for = {
        "missing_authorization": ("submit_authorization_request", "High"),
        "missing_documentation": ("request_documentation", "High"),
        "code_mismatch": ("resubmit_corrected_coding", "Medium"),
        "overdue_follow_up": ("payer_status_follow_up", "Medium"),
    }
    for i, (name, px, amt, stt, risk, issues, *_rest) in enumerate(claim_specs):
        c = claims[i]
        for t, sev in issues:
            ins("claim_issues", [{"organization_id": oid, "claim_id": c["id"], "issue_type": t,
                                  "severity": sev, "description": issue_desc[t], "evidence": {"seeded": True}}])
        if issues and stt in ("reasoned", "awaiting_approval", "denied"):
            top = issues[0][0]
            act, conf = rec_for[top]
            low = len(issues) >= 3
            ins("recommendations", [{
                "organization_id": oid, "claim_id": c["id"], "action_type": act,
                "confidence": conf if not low else "Low", "low_confidence": low,
                "rationale": f"{top.replace('_', ' ').capitalize()} is the top issue on this claim.",
                "cited_issue_types": [t for t, _ in issues],
                "approval_status": "pending" if stt == "awaiting_approval" else "approved",
            }])
        if stt == "actioned":
            ins("follow_ups", [{"organization_id": oid, "claim_id": c["id"], "kind": "payer_reminder",
                                "note": "Opened a payer status follow-up on this claim.",
                                "due_at": iso(NOW + timedelta(days=14)), "originating_agent": "10-reminder",
                                "simulated_send": True, "sent_at": iso(NOW - timedelta(days=1))}])

    # --- appeals on the two denied claims ---
    denied = [c for c in claims if c["status"] == "denied"]
    for c, st in zip(denied, ["drafted", "appeal_approved"]):
        grounds = [{"source": "rule_engine_issue", "ref": "missing_authorization",
                    "detail": "Rule engine flagged a missing prior authorization."},
                   {"source": "payer_denial_reason", "ref": None, "detail": c["denial_reason"]}]
        res = {}
        reviewed_by = reviewed_at = resolved_at = None
        if st == "appeal_approved":
            res = {"outcome": "approved", "bucket": 22, "reversed_amount": float(c["amount"])}
            reviewed_by = uid; reviewed_at = iso(NOW - timedelta(days=2)); resolved_at = iso(NOW - timedelta(days=1))
        ins("appeals", [{
            "organization_id": oid, "claim_id": c["id"], "denial_reason": c["denial_reason"],
            "grounds": grounds, "has_basis": True,
            "letter_text": "To the Appeals Department:\n\nWe are writing to formally appeal the denial "
                           f"of claim {c['claim_id']}. The denial cited a missing prior authorization; "
                           "however, the service was medically necessary and documentation is enclosed. "
                           "We respectfully request reconsideration.\n\nSincerely,\nBilling Department",
            "status": st, "model": "claude-opus-5",
            "submission_payload": {"simulated": True} if st == "appeal_approved" else {},
            "resolution_payload": res, "reviewed_by": reviewed_by, "reviewed_at": reviewed_at,
            "resolved_at": resolved_at,
        }])

    # --- voice reminders (+ contacts + consent) ---
    vr_specs = [
        ("Maria Alvarez", "+15125550143", "confirmed", "confirmed"),
        ("Sofia Rossi", "+15125550188", "confirmed", "confirmed"),
        ("James Okafor", "+15125550111", "reschedule_requested", "reschedule_needed"),
        ("Aisha Rahman", "+15125550166", "no_answer", None),
        ("Priya Nair", "+15125550110", "skipped_no_consent", None),
        ("Noah Fischer", "+15125550199", "pending", None),
    ]
    for name, phone, st, outcome in vr_specs:
        contact = ins("patient_contacts", [{"organization_id": oid, "patient_name": name,
                                            "patient_dob": next(p[1] for p in PATIENTS if p[0] == name),
                                            "phone": phone, "created_by": uid}])[0]
        cons_event = None
        if st != "skipped_no_consent":
            ce = ins("patient_consents", [{"organization_id": oid, "patient_contact_id": contact["id"],
                                           "channel": "voice", "state": "granted", "source": "intake_form",
                                           "recorded_by": uid, "recorded_at": iso(NOW - timedelta(days=5))}])[0]
            cons_event = ce["id"]
        else:
            ins("patient_consents", [{"organization_id": oid, "patient_contact_id": contact["id"],
                                      "channel": "voice", "state": "granted", "source": "intake_form",
                                      "recorded_by": uid, "recorded_at": iso(NOW - timedelta(days=5))}])
            ce = ins("patient_consents", [{"organization_id": oid, "patient_contact_id": contact["id"],
                                           "channel": "voice", "state": "revoked", "source": "patient_request",
                                           "recorded_by": uid, "recorded_at": iso(NOW - timedelta(days=1))}])[0]
        appt_at = next(a["scheduled_at"] for a in appts if a["patient_name"] == name)
        terminal = st not in ("pending",)
        payload = {}
        if outcome:
            payload = {"outcome": outcome, "ended_reason": "assistant-ended-call", "duration_seconds": 37}
        elif st == "no_answer":
            payload = {"outcome": None, "ended_reason": "customer-did-not-answer", "duration_seconds": 12}
        ins("voice_reminders", [{
            "organization_id": oid, "appointment_id": ap[name], "patient_contact_id": contact["id"],
            "patient_name_snapshot": name, "patient_phone_snapshot": phone,
            "clinic_name_snapshot": ORG_NAME, "appointment_at_snapshot": appt_at, "timezone_snapshot": TZ,
            "consent_snapshot": st != "skipped_no_consent",
            "consent_event_id_snapshot": cons_event, "consent_source_snapshot": "intake_form",
            "status": st, "scheduled_call_at": iso(NOW - timedelta(hours=3)),
            "dispatched_at": iso(NOW - timedelta(hours=2)) if terminal else None,
            "authorized_by": uid, "authorized_at": iso(NOW - timedelta(hours=4)),
            "vapi_assistant_id": "ec0bcddf-b32e-4afb-982d-fd0fb852c7e4",
            "vapi_call_id": f"demo-call-{name.split()[0].lower()}" if st in ("confirmed", "reschedule_requested", "no_answer") else None,
            "variable_values": {"clinic_name": ORG_NAME, "patient_name": name.split()[0],
                                "appointment_date": "Thursday, September 11", "appointment_time": "2:30 PM"},
            "outcome": outcome, "outcome_payload": payload,
            "placed_at": iso(NOW - timedelta(hours=2)) if st in ("confirmed", "reschedule_requested", "no_answer") else None,
            "completed_at": iso(NOW - timedelta(hours=2)) if terminal else None,
        }])

    # --- escalations (drive the Tasks queue + Reports 'escalated') ---
    pa_denied = sg("prior_authorizations", {"organization_id": f"eq.{oid}", "status": "eq.auth_denied", "select": "id,appointment_id"})
    vr_resched = sg("voice_reminders", {"organization_id": f"eq.{oid}", "status": "eq.reschedule_requested", "select": "id,appointment_id"})
    escs = [
        {"organization_id": oid, "claim_id": claim_of_patient["Priya Nair"]["id"],
         "reason_code": "appeal_exhausted_needs_human", "originating_agent": "11-appeals",
         "context": {"note": "seeded demo"}},
        {"organization_id": oid, "claim_id": claim_of_patient["Daniel Cho"]["id"],
         "reason_code": "low_confidence_recommendation", "originating_agent": "00-commander",
         "context": {"note": "seeded demo"}},
    ]
    if pa_denied:
        escs.append({"organization_id": oid, "reason_code": "prior_auth_needs_human",
                     "originating_agent": "02-prior-auth", "appointment_id": pa_denied[0]["appointment_id"],
                     "prior_authorization_id": pa_denied[0]["id"], "context": {"note": "seeded demo"}})
    if vr_resched:
        escs.append({"organization_id": oid, "reason_code": "voice_reminder_outcome_needs_human",
                     "originating_agent": "12-escalation", "appointment_id": vr_resched[0]["appointment_id"],
                     "voice_reminder_id": vr_resched[0]["id"], "context": {"outcome": "reschedule_needed"}})
    made_escs = ins("escalations", escs)

    # --- activity log (so Reports automation numbers are non-zero) ---
    al = []
    for c in claims:
        al.append({"organization_id": oid, "claim_id": c["id"], "actor": "00-commander",
                   "action": "needs_analysis", "details": {}})
    for c in claims:
        if c["status"] in ("actioned",):
            al.append({"organization_id": oid, "claim_id": c["id"], "actor": "10-reminder",
                       "action": "executed", "details": {"kind": "payer_reminder"}})
        if c["status"] == "awaiting_approval":
            al.append({"organization_id": oid, "claim_id": c["id"], "actor": f"human:{uid}",
                       "action": "human.approved", "details": {}})
    for e in made_escs:
        al.append({"organization_id": oid, "claim_id": e.get("claim_id"), "actor": "12-escalation",
                   "action": "escalated", "details": {"reason_code": e["reason_code"]}})
    al.append({"organization_id": oid, "actor": "02-prior-auth:submit", "action": "response",
               "details": {"response_status": "approved"}})
    al.append({"organization_id": oid, "actor": "11-appeals:submit", "action": "resolution",
               "details": {"outcome": "approved"}})
    ins("activity_log", al)

    print(f"\n  Demo clinic seeded: {ORG_NAME}  ({oid})")
    print(f"  Login:  {EMAIL}  /  {PW}\n")
    print(f"   {len(PATIENTS)} patients · {len(appts)} appointments · {len(claims)} claims "
          f"({sum(1 for c in claims if c['status']=='denied')} denied) · {len(pas)} prior auths")
    print(f"   {len(covs)} coverages · {len(vr_specs)} reminder calls · {len(made_escs)} escalations")
    print("   Every section — Dashboard, Patients, Appointments, Insurance, Prior Auth, Reminders,")
    print("   Claims, Front Desk, Tasks, Reports — now has content.\n")


if __name__ == "__main__":
    main()
