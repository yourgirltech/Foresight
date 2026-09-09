#!/usr/bin/env python3
"""Seed voice appointment reminders for the Foresight Phase 6 module (agent 17).

  1. Reuses one seed clinic (real signup / bootstrap path) and sets its timezone.
  2. Creates future appointments.
  3. Builds patient_contacts + a patient_consents ledger across a spectrum:
     most 'granted', one 'granted then revoked', one with NO consent row.
  4. Enrolls the consented ones as voice_reminders (status='pending',
     authorized_by=<a real user>, scheduled_call_at in the PAST so a single
     GET /api/automation/voice-reminders/due surfaces them).

17 is an n8n workflow, not a Commander agent — there is no orchestrator call
here. The n8n side (dial Vapi, mark-calling, post the outcome) is exercised by
the tests, not the seed.

    python scripts/seed_voice_reminders.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TIMEOUT = 30


def _http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _dotenv(path: pathlib.Path) -> dict:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def load_config() -> dict:
    env = {**_dotenv(BACKEND / ".env"), **os.environ}
    cfg = {
        "api_url": env.get("SUPABASE_URL"),
        "anon_key": env.get("SUPABASE_ANON_KEY"),
        "service_role_key": env.get("SUPABASE_SERVICE_ROLE_KEY"),
        "jwt_secret": env.get("SUPABASE_JWT_SECRET"),
    }
    if not all(cfg.values()):
        out = subprocess.run(
            ["npx", "--yes", "supabase", "status", "-o", "json"],
            capture_output=True, text=True, timeout=90, shell=(os.name == "nt"),
        ).stdout
        status = {k.upper(): v for k, v in json.loads(out).items()}
        for k, sk in (("api_url", "API_URL"), ("anon_key", "ANON_KEY"),
                      ("service_role_key", "SERVICE_ROLE_KEY"), ("jwt_secret", "JWT_SECRET")):
            cfg[k] = cfg[k] or status.get(sk)
    cfg["api_url"] = (cfg["api_url"] or "http://127.0.0.1:54321").rstrip("/")
    if not cfg["service_role_key"]:
        sys.exit("could not resolve the Supabase service_role key — is the local stack up?")
    return cfg


CFG = load_config()
os.environ.setdefault("SUPABASE_URL", CFG["api_url"])
os.environ.setdefault("SUPABASE_ANON_KEY", CFG["anon_key"] or "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", CFG["service_role_key"])
os.environ.setdefault("SUPABASE_JWT_SECRET", CFG["jwt_secret"] or "")

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"
EMAIL = "voice-seed@foresight.test"
ORG_NAME = "Maple Family Practice (voice seed)"
TZ = "America/Los_Angeles"


def _adm():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def sg(path, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return _http("GET", f"{REST}/{path}?{q}", _adm())[1] or []


def sw(method, path, params, body):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**_adm(), "Prefer": "return=representation"}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org() -> tuple[str, str]:
    """Returns (org_id, user_id). Real bootstrap path, idempotent."""
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _adm())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}
    existing = sg("organizations", {"name": f"eq.{ORG_NAME}", "select": "id"})
    if existing:
        org_id = existing[0]["id"]
        uid = by_email.get(EMAIL)
        if uid is None:
            uid = _http("POST", f"{AUTH}/admin/users", _adm(),
                        {"email": EMAIL, "password": SEED_PW, "email_confirm": True})[1]["id"]
            sw("PATCH", "profiles", {"id": f"eq.{uid}"},
               {"organization_id": org_id, "role": "clinic_admin"})
        return org_id, uid
    if EMAIL not in by_email:
        _http("POST", f"{AUTH}/admin/users", _adm(),
              {"email": EMAIL, "password": SEED_PW, "email_confirm": True})
    tok = _http("POST", f"{AUTH}/token?grant_type=password",
                {"apikey": ANON}, {"email": EMAIL, "password": SEED_PW})[1]["access_token"]
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {tok}"}, {"org_name": ORG_NAME})
    if st != 200:
        sys.exit(f"bootstrap_organization failed: {st} {body}")
    org_id = body["id"]
    uid = _http("GET", f"{AUTH}/admin/users?per_page=200", _adm())[1]
    uid = {u["email"]: u["id"] for u in uid.get("users", [])}[EMAIL]
    return org_id, uid


def wipe(org_id: str) -> None:
    ids = [v["id"] for v in sg("voice_reminders", {"organization_id": f"eq.{org_id}", "select": "id"})]
    for vid in ids:
        sw("DELETE", "escalations", {"organization_id": f"eq.{org_id}", "voice_reminder_id": f"eq.{vid}"}, None)
    sw("DELETE", "activity_log", {"organization_id": f"eq.{org_id}", "action": "like.voice_*"}, None)
    for tbl in ("voice_reminders", "patient_consents", "patient_contacts"):
        sw("DELETE", tbl, {"organization_id": f"eq.{org_id}"}, None)
    sw("DELETE", "appointments", {"organization_id": f"eq.{org_id}", "patient_member_id": "eq.VSEED"}, None)


def main() -> None:
    from zoneinfo import ZoneInfo
    org_id, uid = ensure_org()
    sw("PATCH", "organizations", {"id": f"eq.{org_id}"}, {"timezone": TZ})
    wipe(org_id)
    now = datetime.now(timezone.utc)
    tz = ZoneInfo(TZ)

    def local_appt(days_ahead: int, hour: int, minute: int) -> str:
        d = (now.astimezone(tz) + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        return d.astimezone(timezone.utc).isoformat()

    # (patient, phone, appointment_at, consent, scheduled_call_at)
    people = [
        ("Maria Alvarez", "+14155550142", local_appt(1, 14, 30), "granted", -2),
        ("James Okafor", "+14155550187", local_appt(1, 9, 0), "granted", -1),
        ("Priya Nair", "+14155550110", local_appt(2, 11, 15), "granted_then_revoked", -3),
        ("Daniel Cho", "+14155550166", local_appt(1, 16, 0), "none", None),
    ]

    made = []
    for name, phone, appt_at, consent_kind, due_off in people:
        appt = sw("POST", "appointments", {}, {
            "organization_id": org_id, "patient_name": name,
            "patient_member_id": "VSEED", "patient_phone": phone,
            "scheduled_at": appt_at,
            "is_emergency": False,
        })[0]

        contact = sw("POST", "patient_contacts", {}, {
            "organization_id": org_id, "patient_name": name, "phone": phone, "created_by": uid,
        })[0]

        if consent_kind in ("granted", "granted_then_revoked"):
            sw("POST", "patient_consents", {}, {
                "organization_id": org_id, "patient_contact_id": contact["id"],
                "channel": "voice", "state": "granted", "source": "intake_form",
                "recorded_by": uid, "recorded_at": (now - timedelta(days=3)).isoformat(),
            })
        cur_event = None
        if consent_kind == "granted_then_revoked":
            sw("POST", "patient_consents", {}, {
                "organization_id": org_id, "patient_contact_id": contact["id"],
                "channel": "voice", "state": "revoked", "source": "patient_request",
                "recorded_by": uid, "recorded_at": (now - timedelta(hours=6)).isoformat(),
            })

        if consent_kind == "none":
            made.append((name, "no consent row — not enrolled"))
            continue

        granted = consent_kind == "granted"
        rows = sg("patient_consents", {"patient_contact_id": f"eq.{contact['id']}",
                  "select": "id,state", "order": "recorded_at.desc", "limit": 1})
        cur_event = rows[0]["id"] if rows else None

        vr = sw("POST", "voice_reminders", {}, {
            "organization_id": org_id, "appointment_id": appt["id"],
            "patient_contact_id": contact["id"],
            "patient_name_snapshot": name,
            "patient_phone_snapshot": phone,
            "clinic_name_snapshot": ORG_NAME,
            "appointment_at_snapshot": appt["scheduled_at"],
            "timezone_snapshot": TZ,
            "consent_snapshot": True,   # true at enrollment for both; the revoked one flips at /due
            "consent_event_id_snapshot": cur_event,
            "consent_source_snapshot": "intake_form",
            "status": "pending",
            "scheduled_call_at": (now + timedelta(hours=due_off)).isoformat(),
            "authorized_by": uid,
            "authorized_at": now.isoformat(),
        })[0]
        made.append((name, f"voice_reminder {vr['id'][:8]} pending, due {due_off}h "
                           f"({'consent live' if granted else 'consent REVOKED post-enroll'})"))

    print(f"\norg: {ORG_NAME}  ({org_id})   timezone={TZ}")
    print(f"authorizing user: {EMAIL}  ({uid})\n")
    for name, note in made:
        print(f"  {name:32s}  {note}")
    print("\nNow: GET /api/automation/voice-reminders/due  (Bearer N8N_SERVICE_TOKEN)")
    print("  expect 2 rows returned (Maria, James), Priya -> skipped_no_consent + escalation, Daniel not enrolled.\n")


if __name__ == "__main__":
    main()
