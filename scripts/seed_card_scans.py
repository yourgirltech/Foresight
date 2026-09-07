#!/usr/bin/env python3
"""Seed insurance card scans for the Foresight Front Desk module (Phase 4 / 03).

What it does:
  1. Reuses the two seed clinics (real signup / bootstrap path).
  2. Ensures a couple of seed payers exist (so payer-name match on confirm has
     something to hit).
  3. Wipes card_scans + the Storage objects + the appointments this script made
     (name prefix "CardScan-Seed:") so re-runs are clean.
  4. Uploads the synthetic, clearly-fake card fixtures (tests/_cards.py) to the
     private card-scans bucket and creates card_scans rows across every status:
     extracted / needs_review / confirmed / rejected / error.
  5. Confirms one scan against an appointment (exercises THE ONLY write-back
     path: appointments.patient_member_id + a payer_name match) and rejects one.
  6. Prints the status distribution.

With ANTHROPIC_API_KEY set it runs the real vision extraction; without it, it
uses a canned extraction derived from each fixture's known values (so the demo
data is still realistic offline). Pass --live to force the real call.

Usage:
    python scripts/seed_card_scans.py
    python scripts/seed_card_scans.py --live
"""
from __future__ import annotations

import argparse
import asyncio
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
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_config() -> dict:
    env = {**_dotenv(BACKEND / ".env"), **os.environ}
    cfg = {
        "api_url": env.get("SUPABASE_URL"),
        "anon_key": env.get("SUPABASE_ANON_KEY"),
        "service_role_key": env.get("SUPABASE_SERVICE_ROLE_KEY"),
        "jwt_secret": env.get("SUPABASE_JWT_SECRET"),
        "anthropic_api_key": env.get("ANTHROPIC_API_KEY", ""),
    }
    if not all(cfg[k] for k in ("api_url", "anon_key", "service_role_key", "jwt_secret")):
        out = subprocess.run(
            ["npx", "--yes", "supabase", "status", "-o", "json"],
            capture_output=True, text=True, timeout=90, shell=(os.name == "nt"),
        ).stdout
        status = {k.upper(): v for k, v in json.loads(out).items()}
        cfg["api_url"] = cfg["api_url"] or status.get("API_URL")
        cfg["anon_key"] = cfg["anon_key"] or status.get("ANON_KEY")
        cfg["service_role_key"] = cfg["service_role_key"] or status.get("SERVICE_ROLE_KEY")
        cfg["jwt_secret"] = cfg["jwt_secret"] or status.get("JWT_SECRET")
    cfg["api_url"] = (cfg["api_url"] or "http://127.0.0.1:54321").rstrip("/")
    if not cfg["service_role_key"]:
        sys.exit("could not resolve the Supabase service_role key — is the local stack up?")
    return cfg


CFG = load_config()
os.environ.setdefault("SUPABASE_URL", CFG["api_url"])
os.environ.setdefault("SUPABASE_ANON_KEY", CFG["anon_key"] or "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", CFG["service_role_key"])
os.environ.setdefault("SUPABASE_JWT_SECRET", CFG["jwt_secret"] or "")
if CFG["anthropic_api_key"]:
    os.environ.setdefault("ANTHROPIC_API_KEY", CFG["anthropic_api_key"])

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "tests"))
from app.agents import db, ocr  # noqa: E402
from app.agents.ocr import CONFIRM_IMAGE_RETENTION_DAYS, CardExtraction, classify_extraction  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.storage import upload_object  # noqa: E402

from _cards import synthetic_card  # noqa: E402

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"
NAME_PREFIX = "CardScan-Seed:"
BUCKET = "card-scans"


def _admin_headers():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def svc_get(path, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    _, body = _http("GET", f"{REST}/{path}?{q}", _admin_headers())
    return body or []


def svc_write(method, path, params, body):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**_admin_headers(), "Prefer": "return=representation"}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org(email: str, org_name: str) -> str:
    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin_headers())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}
    if existing:
        org_id = existing[0]["id"]
        uid = by_email.get(email)
        if uid is None:
            _, body = _http("POST", f"{AUTH}/admin/users", _admin_headers(),
                            {"email": email, "password": SEED_PW, "email_confirm": True})
            uid = body["id"]
            svc_write("PATCH", "profiles", {"id": f"eq.{uid}"},
                      {"organization_id": org_id, "role": "clinic_admin"})
        return org_id, by_email.get(email)
    if email not in by_email:
        _http("POST", f"{AUTH}/admin/users", _admin_headers(),
              {"email": email, "password": SEED_PW, "email_confirm": True})
    _, body = _http("POST", f"{AUTH}/token?grant_type=password",
                    {"apikey": ANON}, {"email": email, "password": SEED_PW})
    token = body["access_token"]
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {token}"}, {"org_name": org_name})
    if st != 200:
        sys.exit(f"bootstrap_organization failed for {org_name}: {st} {body}")
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin_headers())[1] or {}
    uid = {u["email"]: u["id"] for u in users.get("users", [])}.get(email)
    return body["id"], uid


SEED_PAYERS = ["Northwind Health", "Cascade Medicaid"]


def ensure_payers(org_id: str) -> dict[str, dict]:
    have = {p["name"]: p for p in svc_get("payers", {"organization_id": f"eq.{org_id}", "select": "*"})}
    out = {}
    for name in SEED_PAYERS:
        if name in have:
            out[name] = have[name]
        else:
            out[name] = svc_write("POST", "payers", {}, {
                "organization_id": org_id, "name": name,
                "authorization_required": False, "documentation_required": False,
            })[0]
    return out


def wipe(org_id: str) -> None:
    for scan in svc_get("card_scans", {"organization_id": f"eq.{org_id}", "select": "image_path"}):
        q = urllib.parse.quote(scan["image_path"])
        _http("DELETE", f"{API}/storage/v1/object/{BUCKET}/{q}", _admin_headers())
    svc_write("DELETE", "card_scans", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "appointments",
              {"organization_id": f"eq.{org_id}", "patient_name": f"like.{NAME_PREFIX}*"}, None)
    svc_write("DELETE", "activity_log",
              {"organization_id": f"eq.{org_id}", "action": "like.card_scan_*"}, None)


def canned_extraction(spec: dict) -> CardExtraction:
    """A deterministic stand-in for the vision call when there is no API key —
    built straight from the fixture's known values."""
    fields, confidence = {}, {}
    for f in ocr.FIELDS:
        value = spec.get(f)
        illegible = f in spec.get("illegible", ())
        absent = f in spec.get("absent", ())
        if illegible or absent:
            value = None
        fields[f] = value
        confidence[f] = {
            "confidence": "high" if value else "low",
            "legible": value is not None,
            "absent": absent,
        }
    return CardExtraction(fields=fields, confidence=confidence, model="seed-canned")


async def make_appointment(org_id: str, name: str, payer_id: str | None) -> str:
    appt = await db.insert_appointment(org_id, {
        "patient_name": f"{NAME_PREFIX} {name}", "patient_member_id": "",
        "payer_id": payer_id,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        "is_emergency": False,
    })
    return appt["id"]


async def run_scan(org_id: str, kind: str, patient: str, *, appointment_id: str | None,
                   live: bool) -> dict:
    png, spec = synthetic_card(kind)
    import uuid as _uuid
    scan_id = str(_uuid.uuid4())
    path = f"{org_id}/{scan_id}.png"
    await upload_object(BUCKET, path, png, "image/png")

    row = await db.insert_card_scan(org_id, {
        "id": scan_id, "appointment_id": appointment_id,
        "patient_name": f"{NAME_PREFIX} {patient}",
        "image_path": path, "image_mime": "image/png", "status": "pending",
    })

    if live and get_settings().anthropic_api_key:
        try:
            ext = await ocr.extract(png, "image/png")
        except ocr.OcrUnavailable as exc:
            return await db.update_card_scan(org_id, scan_id, {
                "status": "error", "extracted_fields": {"error": str(exc)}, "field_confidence": {}})
    else:
        ext = canned_extraction(spec)

    new_status = classify_extraction(ext, floor=get_settings().ocr_confidence_floor)
    return await db.update_card_scan(org_id, scan_id, {
        "status": new_status, "extracted_fields": ext.fields,
        "field_confidence": ext.confidence, "model": ext.model})


async def confirm(org_id: str, scan: dict, uid: str, payers: dict) -> dict:
    """Mirror POST /api/card-scans/{id}/confirm: write member id + a payer match
    onto the linked appointment, mark confirmed, stamp the retention window."""
    fields = scan["extracted_fields"]
    confirmed = {k: fields.get(k) for k in ocr.FIELDS}
    appt_id = scan.get("appointment_id")
    applied = False
    matched = None
    if appt_id:
        update = {}
        if confirmed.get("member_id"):
            update["patient_member_id"] = confirmed["member_id"]
        if confirmed.get("payer_name"):
            want = confirmed["payer_name"].strip().lower()
            matched = next((p["id"] for n, p in payers.items() if n.strip().lower() == want), None)
            if matched:
                update["payer_id"] = matched
        if update:
            await db.update_appointment(org_id, appt_id, update)
            applied = True
    now = datetime.now(timezone.utc)
    row = await db.update_card_scan(org_id, scan["id"], {
        "status": "confirmed",
        "extracted_fields": {**fields, "confirmed": confirmed},
        "reviewed_by": uid, "reviewed_at": now.isoformat(),
        "applied_to_appointment": applied,
        "image_retain_until": (now + timedelta(days=CONFIRM_IMAGE_RETENTION_DAYS)).isoformat(),
    })
    await db.insert_activity(org_id, None, actor=f"human:{uid}", action="card_scan_confirmed",
                             appointment_id=appt_id,
                             details={"card_scan_id": scan["id"], "applied_to_appointment": applied,
                                      "matched_payer_id": matched})
    return row


async def reject(org_id: str, scan: dict, uid: str) -> dict:
    q = urllib.parse.quote(scan["image_path"])
    _http("DELETE", f"{API}/storage/v1/object/{BUCKET}/{q}", _admin_headers())
    row = await db.update_card_scan(org_id, scan["id"], {
        "status": "rejected", "reviewed_by": uid,
        "reviewed_at": datetime.now(timezone.utc).isoformat()})
    await db.insert_activity(org_id, None, actor=f"human:{uid}", action="card_scan_rejected",
                             appointment_id=scan.get("appointment_id"),
                             details={"card_scan_id": scan["id"]})
    return row


SEED_ORGS = [
    ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
    ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
]


async def seed_org(email: str, org_name: str, *, live: bool, full: bool) -> dict:
    org_id, uid = ensure_org(email, org_name)
    wipe(org_id)
    payers = ensure_payers(org_id)
    nw = payers["Northwind Health"]

    scans: list[dict] = []

    # extracted, linked to an appointment (ready for a human to confirm)
    a1 = await make_appointment(org_id, "Jordan Avery", nw["id"])
    scans.append(await run_scan(org_id, "clean", "Jordan Avery", appointment_id=a1, live=live))

    # a clean scan we then CONFIRM against its appointment -> write-back
    a2 = await make_appointment(org_id, "Priya Chandra", None)
    s2 = await run_scan(org_id, "clean", "Priya Chandra", appointment_id=a2, live=live)
    scans.append(await confirm(org_id, s2, uid, payers))

    # needs_review: a blurry member id
    scans.append(await run_scan(org_id, "blurry-member-id", "Marcus Bell", appointment_id=None, live=live))

    if full:
        # needs_review: plan type not printed and (canned) not flagged absent
        scans.append(await run_scan(org_id, "no-plan-type", "Dana Ito", appointment_id=None, live=live))

        # rejected: someone photographed the back of the card
        s5 = await run_scan(org_id, "wrong-card-back", "Sam Okafor", appointment_id=None, live=live)
        scans.append(await reject(org_id, s5, uid))

        # error: the vision call was unavailable at scan time
        import uuid as _uuid
        eid = str(_uuid.uuid4())
        png, _ = synthetic_card("clean")
        await upload_object(BUCKET, f"{org_id}/{eid}.png", png, "image/png")
        await db.insert_card_scan(org_id, {
            "id": eid, "appointment_id": None, "patient_name": f"{NAME_PREFIX} Robin Vale",
            "image_path": f"{org_id}/{eid}.png", "image_mime": "image/png", "status": "pending"})
        scans.append(await db.update_card_scan(org_id, eid, {
            "status": "error",
            "extracted_fields": {"error": "ANTHROPIC_API_KEY was not set when this scan ran"},
            "field_confidence": {}}))

    return {"org_id": org_id, "org_name": org_name, "email": email, "scans": scans}


def report(res: dict) -> None:
    rows = svc_get("card_scans", {"organization_id": f"eq.{res['org_id']}",
                                  "select": "status,applied_to_appointment"})
    dist: dict[str, int] = {}
    for r in rows:
        dist[r["status"]] = dist.get(r["status"], 0) + 1
    applied = sum(1 for r in rows if r["applied_to_appointment"])
    print(f"\n  {res['org_name']} — {len(rows)} card scans")
    for k in ("extracted", "needs_review", "confirmed", "rejected", "error", "pending"):
        if dist.get(k):
            print(f"    {k:<14} {dist[k]}")
    print(f"    (applied_to_appointment = true on {applied})")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="call the real Claude vision API (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    key = bool(get_settings().anthropic_api_key)
    print("Foresight — seeding insurance card scans (Phase 4 / 03)")
    print(f"  Supabase : {API}")
    print(f"  vision   : {'live Claude vision' if (args.live and key) else 'canned extraction (offline)'}\n")

    a = await seed_org(*SEED_ORGS[0], live=args.live, full=True)
    b = await seed_org(*SEED_ORGS[1], live=args.live, full=False)

    print("=" * 64)
    report(a)
    report(b)
    print("\nLog in to review at /app/front-desk:")
    for email, _ in SEED_ORGS:
        print(f"    {email}  /  {SEED_PW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
