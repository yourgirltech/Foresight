#!/usr/bin/env python3
"""Seed patient coverages for the Foresight coordination-of-benefits module (04).

What it does:
  1. Reuses the two seed clinics (real signup / bootstrap path).
  2. Ensures a handful of seed payers exist.
  3. Wipes patient_coverages + the appointments this script created (name prefix
     "COB-Seed:") so re-runs are clean.
  4. Creates one appointment per demo patient and a set of coverages that
     exercises each rule R0-R7, then prints which rule decided each patient's
     primary (via the pure determine_cob / cob_summary).

04 has no LLM and no simulation — this is pure data + the deterministic engine.
No ANTHROPIC_API_KEY needed.

Usage:
    python scripts/seed_coverages.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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
    }
    if not all(cfg.values()):
        out = subprocess.run(
            ["npx", "--yes", "supabase", "status", "-o", "json"],
            capture_output=True, text=True, timeout=90, shell=(os.name == "nt"),
        ).stdout
        s = {k.upper(): v for k, v in json.loads(out).items()}
        cfg["api_url"] = cfg["api_url"] or s.get("API_URL")
        cfg["anon_key"] = cfg["anon_key"] or s.get("ANON_KEY")
        cfg["service_role_key"] = cfg["service_role_key"] or s.get("SERVICE_ROLE_KEY")
        cfg["jwt_secret"] = cfg["jwt_secret"] or s.get("JWT_SECRET")
    cfg["api_url"] = (cfg["api_url"] or "http://127.0.0.1:54321").rstrip("/")
    if not cfg["service_role_key"]:
        sys.exit("could not resolve the Supabase service_role key — is the local stack up?")
    return cfg


CFG = load_config()
os.environ.setdefault("SUPABASE_URL", CFG["api_url"])
os.environ.setdefault("SUPABASE_ANON_KEY", CFG["anon_key"] or "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", CFG["service_role_key"])
os.environ.setdefault("SUPABASE_JWT_SECRET", CFG["jwt_secret"] or "")

sys.path.insert(0, str(BACKEND))
from app.agents import cob, db  # noqa: E402

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"
NAME_PREFIX = "COB-Seed:"
TODAY = datetime.now(timezone.utc).date()


def _admin():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def svc_get(path, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return _http("GET", f"{REST}/{path}?{q}", _admin())[1] or []


def svc_write(method, path, params, body):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**_admin(), "Prefer": "return=representation"}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org(email: str, org_name: str) -> str:
    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}
    if existing:
        org_id = existing[0]["id"]
        if email not in by_email:
            _, body = _http("POST", f"{AUTH}/admin/users", _admin(),
                            {"email": email, "password": SEED_PW, "email_confirm": True})
            svc_write("PATCH", "profiles", {"id": f"eq.{body['id']}"},
                      {"organization_id": org_id, "role": "clinic_admin"})
        return org_id
    if email not in by_email:
        _http("POST", f"{AUTH}/admin/users", _admin(),
              {"email": email, "password": SEED_PW, "email_confirm": True})
    _, body = _http("POST", f"{AUTH}/token?grant_type=password",
                    {"apikey": ANON}, {"email": email, "password": SEED_PW})
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {body['access_token']}"},
                     {"org_name": org_name})
    if st != 200:
        sys.exit(f"bootstrap_organization failed for {org_name}: {st} {body}")
    return body["id"]


SEED_PAYERS = ["Northwind Health", "BlueRidge PPO", "Cascade Medicaid", "Evergreen Individual",
               "Summit Retiree Plan", "Medicare"]


def ensure_payers(org_id: str) -> None:
    have = {p["name"] for p in svc_get("payers", {"organization_id": f"eq.{org_id}", "select": "name"})}
    for name in SEED_PAYERS:
        if name not in have:
            svc_write("POST", "payers", {}, {
                "organization_id": org_id, "name": name,
                "authorization_required": False, "documentation_required": False})


def wipe(org_id: str) -> None:
    svc_write("DELETE", "patient_coverages", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "appointments",
              {"organization_id": f"eq.{org_id}", "patient_name": f"like.{NAME_PREFIX}*"}, None)


def _c(**kw) -> dict:
    d = {
        "payer_name": "Payer", "member_id": "M", "group_number": "G",
        "plan_kind": "medical", "coverage_type": "employer_active",
        "relationship_to_subscriber": "self", "is_dependent": False,
        "subscriber_name": "", "subscriber_dob": None,
        "effective_date": "2020-01-01", "termination_date": None,
        "manual_order_override": None,
    }
    d.update(kw)
    return d


# name -> list of coverage dicts (before org/patient are stamped)
SCENARIOS: dict[str, list[dict]] = {
    "Rae Nolan (R1 — own vs dependent)": [
        _c(payer_name="Northwind Health", coverage_type="employer_active"),
        _c(payer_name="BlueRidge PPO", relationship_to_subscriber="spouse", is_dependent=True,
           subscriber_name="Sky Nolan"),
    ],
    "Theo Park (R2 — Medicaid last)": [
        _c(payer_name="BlueRidge PPO", coverage_type="employer_active"),
        _c(payer_name="Cascade Medicaid", coverage_type="medicaid"),
    ],
    "Mira Osei (R3 — birthday rule)": [
        _c(payer_name="Northwind Health", relationship_to_subscriber="child", is_dependent=True,
           subscriber_name="Ada Osei", subscriber_dob="1989-03-04"),
        _c(payer_name="BlueRidge PPO", relationship_to_subscriber="child", is_dependent=True,
           subscriber_name="Kwame Osei", subscriber_dob="1987-10-22"),
    ],
    "Sol Vance (R4 — active over COBRA)": [
        _c(payer_name="Evergreen Individual", coverage_type="cobra"),
        _c(payer_name="Northwind Health", coverage_type="employer_active",
           relationship_to_subscriber="spouse", is_dependent=True, subscriber_name="Bay Vance"),
    ],
    "Iris Bloom (R5 — Medicare secondary to active)": [
        _c(payer_name="Northwind Health", coverage_type="employer_active"),
        _c(payer_name="Medicare", coverage_type="medicare"),
    ],
    "Otis Crane (R5 — Medicare primary over retiree)": [
        _c(payer_name="Summit Retiree Plan", coverage_type="employer_retiree"),
        _c(payer_name="Medicare", coverage_type="medicare"),
    ],
    "Wren Ash (R6 — longer-covered)": [
        _c(payer_name="Evergreen Individual", coverage_type="individual", effective_date="2016-01-01"),
        _c(payer_name="BlueRidge PPO", coverage_type="individual", effective_date="2023-01-01"),
    ],
    "Fern Lake (R7 — no distinguishing rule)": [
        _c(payer_name="Evergreen Individual", coverage_type="individual", effective_date="2021-06-01"),
        _c(payer_name="Acme Mutual", coverage_type="individual", effective_date="2021-06-01"),
    ],
    "Dell Hart (R0 — staff pin)": [
        _c(payer_name="Northwind Health", coverage_type="employer_active", manual_order_override=2),
        _c(payer_name="Cascade Medicaid", coverage_type="medicaid", manual_order_override=1),
    ],
    "Bex Frost (3-way — own + spouse + Medicaid)": [
        _c(payer_name="Northwind Health", coverage_type="employer_active"),
        _c(payer_name="BlueRidge PPO", relationship_to_subscriber="spouse", is_dependent=True,
           subscriber_name="Ari Frost"),
        _c(payer_name="Cascade Medicaid", coverage_type="medicaid"),
    ],
}


async def seed_org(email: str, org_name: str) -> dict:
    org_id = ensure_org(email, org_name)
    wipe(org_id)
    ensure_payers(org_id)

    results = []
    for label, covers in SCENARIOS.items():
        patient = f"{NAME_PREFIX} {label.split(' (')[0]}"
        dob = "1990-01-01"
        appt = await db.insert_appointment(org_id, {
            "patient_name": patient, "patient_member_id": "", "patient_dob": dob,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            "is_emergency": False})
        rows = []
        for cvr in covers:
            rows.append(await db.insert_patient_coverage(org_id, {
                **cvr, "patient_name": patient, "patient_dob": dob, "appointment_id": appt["id"],
                "subscriber_name": cvr["subscriber_name"] or patient}))
        summary = cob.cob_summary(rows, today=TODAY, patient_dob=date(1990, 1, 1))
        med = summary.get("medical", [])
        primary = next((p for p in med if p["order"] == "primary"), None)
        results.append((label, primary, med))
    return {"org_id": org_id, "org_name": org_name, "results": results}


def report(res: dict) -> None:
    print(f"\n  {res['org_name']}")
    for label, primary, med in res["results"]:
        rule = primary["rule"] if primary else "—"
        n_primary = sum(1 for p in med if p["order"] == "primary")
        flag = " " if n_primary == 1 else " !EXACTLY-ONE-PRIMARY-VIOLATED! "
        print(f"    {label:<44}{flag}primary decided by {rule or '(sole)'}"
              f"   [{' > '.join(p['order'][0].upper() for p in med)}]")


async def main() -> int:
    print("Foresight — seeding patient coverages (Phase 4 / 04)")
    print(f"  Supabase : {API}")
    print("  04 coordination of benefits: pure deterministic engine, no API key\n")

    orgs = [
        ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
        ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
    ]
    a = await seed_org(*orgs[0])
    b = await seed_org(*orgs[1])
    print("=" * 68)
    report(a)
    report(b)

    all_ok = all(
        primary is not None and sum(1 for p in med if p["order"] == "primary") == 1
        for r in (a, b) for _, primary, med in r["results"]
    )
    print("\n  " + ("OK  " if all_ok else "!   ")
          + "every seeded patient has exactly one primary, each with a cited rule")
    print("\nLog in to review on an appointment's detail page (/app/appointments):")
    for email, _ in orgs:
        print(f"    {email}  /  {SEED_PW}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
