#!/usr/bin/env python3
"""Seed ONE voice reminder, due now, for a live end-to-end test.

Creates (idempotently) a dedicated test clinic + admin user, then one
appointment + patient_contact + a GRANTED voice-consent ledger row + one
voice_reminders row with status='pending' and scheduled_call_at in the past, so
the next automatic GET /api/automation/voice-reminders/due surfaces it and the
n8n workflow dials it.

    python scripts/seed_one_voice_reminder.py --phone +2349036423694 --name "Funmilola" \
        --clinic "Riverside Health Clinic" --tz Africa/Lagos --appt-in-hours 26

Prints exactly what will be spoken so it can be vetoed before the poll fires.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _dotenv(path):
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def load_cfg():
    env = {**_dotenv(BACKEND / ".env"), **os.environ}
    cfg = {"api": env.get("SUPABASE_URL"), "anon": env.get("SUPABASE_ANON_KEY"),
           "svc": env.get("SUPABASE_SERVICE_ROLE_KEY")}
    if not all(cfg.values()):
        out = subprocess.run(["npx", "--yes", "supabase", "status", "-o", "json"],
                             capture_output=True, text=True, timeout=90,
                             shell=(os.name == "nt")).stdout
        s = {k.upper(): v for k, v in json.loads(out).items()}
        cfg["api"] = cfg["api"] or s.get("API_URL")
        cfg["anon"] = cfg["anon"] or s.get("ANON_KEY")
        cfg["svc"] = cfg["svc"] or s.get("SERVICE_ROLE_KEY")
    cfg["api"] = (cfg["api"] or "http://127.0.0.1:54321").rstrip("/")
    if not cfg["svc"]:
        sys.exit("no service_role key — is the local stack up?")
    return cfg


CFG = load_cfg()
REST = f"{CFG['api']}/rest/v1"
AUTH = f"{CFG['api']}/auth/v1"
SVC, ANON = CFG["svc"], CFG["anon"]
PW = "Foresight-seed-123!"


def adm():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def sg(path, params):
    q = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in params.items())
    return _http("GET", f"{REST}/{path}?{q}", adm())[1] or []


def sw(method, path, params, body):
    q = "&".join(f"{k}={urllib.request.quote(str(v), safe='')}" for k, v in params.items())
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**adm(), "Prefer": "return=representation"}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org(email, name):
    users = (_http("GET", f"{AUTH}/admin/users?per_page=200", adm())[1] or {}).get("users", [])
    by_email = {u["email"]: u["id"] for u in users}
    existing = sg("organizations", {"name": f"eq.{name}", "select": "id"})
    if existing:
        oid = existing[0]["id"]
        uid = by_email.get(email)
        if uid is None:
            uid = _http("POST", f"{AUTH}/admin/users", adm(),
                        {"email": email, "password": PW, "email_confirm": True})[1]["id"]
            sw("PATCH", "profiles", {"id": f"eq.{uid}"},
               {"organization_id": oid, "role": "clinic_admin"})
        return oid, uid
    if email not in by_email:
        _http("POST", f"{AUTH}/admin/users", adm(),
              {"email": email, "password": PW, "email_confirm": True})
    tok = _http("POST", f"{AUTH}/token?grant_type=password", {"apikey": ANON},
                {"email": email, "password": PW})[1]["access_token"]
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {tok}"}, {"org_name": name})
    if st != 200:
        sys.exit(f"bootstrap failed: {st} {body}")
    oid = body["id"]
    users = (_http("GET", f"{AUTH}/admin/users?per_page=200", adm())[1] or {}).get("users", [])
    uid = {u["email"]: u["id"] for u in users}[email]
    return oid, uid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--clinic", default="Riverside Health Clinic")
    ap.add_argument("--tz", default="Africa/Lagos")
    ap.add_argument("--appt-in-hours", type=float, default=26.0)
    ap.add_argument("--due-minutes", type=float, default=-3.0, help="scheduled_call_at offset from now")
    ap.add_argument("--email", default="live-voice-test@foresight.test")
    args = ap.parse_args()

    if not E164.match(args.phone):
        sys.exit(f"--phone {args.phone!r} is not E.164 (must be +<countrycode><number>)")
    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)

    oid, uid = ensure_org(args.email, args.clinic)
    sw("PATCH", "organizations", {"id": f"eq.{oid}"}, {"timezone": args.tz})

    # clean any prior run in this org
    for v in sg("voice_reminders", {"organization_id": f"eq.{oid}", "select": "id"}):
        sw("DELETE", "escalations", {"organization_id": f"eq.{oid}", "voice_reminder_id": f"eq.{v['id']}"}, None)
    sw("DELETE", "activity_log", {"organization_id": f"eq.{oid}", "action": "like.voice_*"}, None)
    for t in ("voice_reminders", "patient_consents", "patient_contacts", "appointments"):
        sw("DELETE", t, {"organization_id": f"eq.{oid}"}, None)

    appt_at = now + timedelta(hours=args.appt_in_hours)
    appt = sw("POST", "appointments", {}, {
        "organization_id": oid, "patient_name": args.name, "patient_member_id": "LIVETEST",
        "patient_phone": args.phone, "scheduled_at": appt_at.isoformat(), "is_emergency": False,
    })[0]
    contact = sw("POST", "patient_contacts", {}, {
        "organization_id": oid, "patient_name": args.name, "phone": args.phone, "created_by": uid,
    })[0]
    consent = sw("POST", "patient_consents", {}, {
        "organization_id": oid, "patient_contact_id": contact["id"], "channel": "voice",
        "state": "granted", "source": "verbal_documented", "recorded_by": uid,
        "recorded_at": (now - timedelta(minutes=10)).isoformat(),
        "note": "live end-to-end test — consent captured by seed script",
    })[0]
    call_at = now + timedelta(minutes=args.due_minutes)
    vr = sw("POST", "voice_reminders", {}, {
        "organization_id": oid, "appointment_id": appt["id"], "patient_contact_id": contact["id"],
        "patient_name_snapshot": args.name, "patient_phone_snapshot": args.phone,
        "clinic_name_snapshot": args.clinic, "appointment_at_snapshot": appt_at.isoformat(),
        "timezone_snapshot": args.tz, "consent_snapshot": True,
        "consent_event_id_snapshot": consent["id"], "consent_source_snapshot": "verbal_documented",
        "status": "pending", "scheduled_call_at": call_at.isoformat(),
        "authorized_by": uid, "authorized_at": now.isoformat(),
    })[0]

    local = appt_at.astimezone(tz)
    spoken_date = f"{local.strftime('%A, %B')} {local.day}"
    h12 = local.hour % 12 or 12
    spoken_time = f"{h12}:{local.minute:02d} {local.strftime('%p')}"

    print("\n  SEEDED — one voice_reminder, status=pending, due now\n")
    print(f"  voice_reminder_id : {vr['id']}")
    print(f"  organization      : {args.clinic}  ({oid})")
    print(f"  scheduled_call_at : {call_at.isoformat()}   ({-args.due_minutes:.0f} min ago -> next poll picks it up)")
    print(f"  status            : pending   authorized_by set: {bool(vr['authorized_by'])}   consent: GRANTED\n")
    print("  the call will dial   : " + args.phone)
    print("  and speak these four variables:")
    print(f"     clinic_name      = {args.clinic!r}")
    print(f"     patient_name     = {args.name!r}   (first name)")
    print(f"     appointment_date = {spoken_date!r}")
    print(f"     appointment_time = {spoken_time!r}   ({args.tz})\n")


if __name__ == "__main__":
    main()
