#!/usr/bin/env python3
"""Seed appointments + eligibility checks for the Foresight eligibility module (Phase 2).

What it does:
  1. Reuses the two seed clinics (real signup / bootstrap path).
  2. Ensures the four seed payers exist and sets their eligibility knobs
     (docs/agents/01-eligibility-agent.md §7.5) — a documented spread so the
     status distribution is reproducible.
  3. Wipes prior appointments + eligibility checks so re-runs are clean.
  4. Generates scheduled appointments with a seeded RNG (deterministic member
     ids -> deterministic buckets -> deterministic status distribution), plus a
     few deterministic demo appointments, and drives each through
     orchestrator.handle_eligibility.
  5. Registers one EMERGENCY patient with no member id -> insufficient_info,
     then re-checks once an id is "added" -> verified_active, and prints the
     progression.
  6. Prints the eligibility-status distribution and the demo outcomes.

No ANTHROPIC_API_KEY needed — 01 has no LLM step.

Usage:
    python scripts/seed_eligibility.py                 # default 14 + 6 scheduled appts
    python scripts/seed_eligibility.py --a 20 --b 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import random
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

sys.path.insert(0, str(BACKEND))
from app.agents import db, orchestrator  # noqa: E402
from app.agents.eligibility import bucket  # noqa: E402

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"


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


# --------------------------------------------------------------------------- #
# org bootstrap (real signup path, idempotent) — same as seed_claims.py
# --------------------------------------------------------------------------- #
def ensure_org(email: str, org_name: str) -> str:
    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin_headers())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}
    if existing:
        org_id = existing[0]["id"]
        uid = by_email.get(email)
        if uid is None:
            st, body = _http("POST", f"{AUTH}/admin/users", _admin_headers(),
                             {"email": email, "password": SEED_PW, "email_confirm": True})
            uid = body["id"]
            svc_write("PATCH", "profiles", {"id": f"eq.{uid}"},
                      {"organization_id": org_id, "role": "clinic_admin"})
        return org_id
    if email not in by_email:
        _http("POST", f"{AUTH}/admin/users", _admin_headers(),
              {"email": email, "password": SEED_PW, "email_confirm": True})
    st, body = _http("POST", f"{AUTH}/token?grant_type=password",
                     {"apikey": ANON}, {"email": email, "password": SEED_PW})
    token = body["access_token"]
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {token}"}, {"org_name": org_name})
    if st != 200:
        sys.exit(f"bootstrap_organization failed for {org_name}: {st} {body}")
    return body["id"]


# --------------------------------------------------------------------------- #
# payers — the eligibility simulation knobs (01-eligibility-agent.md §7.5)
# --------------------------------------------------------------------------- #
#   name                 : (auth_req, doc_req, follow_up_days, elig_supported, active_threshold)
PAYER_SPECS = {
    "Meridian Health Plan": (True, True, 21, True, 90),   # large commercial, mostly active
    "BlueRidge PPO":        (True, False, 30, True, 82),   # commercial PPO
    "Cascade Medicaid":     (False, True, 14, True, 68),   # Medicaid — more lapsed/termed
    "Summit Commercial":    (False, False, None, False, 0),  # NOT on the eligibility network -> check_failed
}
PAYER_WEIGHTS = {"Meridian Health Plan": 4, "BlueRidge PPO": 3, "Cascade Medicaid": 3, "Summit Commercial": 1}

FIRST = ["Ava", "Liam", "Noah", "Mia", "Ella", "Owen", "Lucas", "Aria", "Leo", "Nora",
         "Kai", "Zoe", "Ivy", "Jude", "Cora", "Max", "Ruby", "Eli", "June", "Sam"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Silva", "Okafor", "Brooks",
        "Reyes", "Haddad", "Weber", "Flores", "Cohen", "Bauer", "Ford", "Mercer", "Ali"]


def ensure_payers(org_id: str) -> dict[str, dict]:
    have = {p["name"]: p for p in svc_get("payers", {"organization_id": f"eq.{org_id}", "select": "*"})}
    out: dict[str, dict] = {}
    for name, (auth_req, doc_req, thr, supported, active) in PAYER_SPECS.items():
        fields = {
            "authorization_required": auth_req, "documentation_required": doc_req,
            "follow_up_threshold_days": thr,
            "eligibility_verification_supported": supported,
            "eligibility_active_threshold": active,
        }
        if name in have:
            out[name] = svc_write("PATCH", "payers",
                                  {"organization_id": f"eq.{org_id}", "id": f"eq.{have[name]['id']}"},
                                  fields)[0]
        else:
            out[name] = svc_write("POST", "payers", {},
                                  {"organization_id": org_id, "name": name, **fields})[0]
    return out


def member_id_for_band(rng: random.Random, payer_id: str, want_active: bool) -> str:
    """Deterministically pick a member id whose bucket lands in the wanted band
    for this payer. Documented + reproducible — no random guess at runtime."""
    thr = PAYER_SPECS  # unused; threshold looked up by caller via payer row
    for _ in range(500):
        mid = f"{rng.choice('ABMPX')}{rng.randint(10**8, 10**9 - 1)}"
        b = bucket(mid, payer_id)
        if want_active and b < 60:      # comfortably active for every supported payer
            return mid
        if not want_active and b >= 95:  # comfortably inactive for every supported payer
            return mid
    return mid  # fallback (shouldn't happen)


# --------------------------------------------------------------------------- #
# driving
# --------------------------------------------------------------------------- #
async def drive_scheduled(org_id: str, payer: dict, patient_name: str, member_id: str,
                          scheduled_at: str) -> str:
    appt = await db.insert_appointment(org_id, {
        "patient_name": patient_name, "patient_member_id": member_id,
        "payer_id": payer["id"], "scheduled_at": scheduled_at, "is_emergency": False,
    })
    check = await db.insert_eligibility_check(org_id, {
        "appointment_id": appt["id"], "patient_name": patient_name,
        "patient_member_id": member_id, "payer_id": payer["id"],
        "payer_name": payer["name"], "is_emergency": False, "status": "pending",
    })
    await orchestrator.handle_eligibility(check["id"], {"type": "appointment_scheduled"})
    row = svc_get("eligibility_checks", {"id": f"eq.{check['id']}", "select": "status"})[0]
    return row["status"]


async def drive_emergency(org_id: str, patient_name: str, member_id: str,
                          payer: dict | None) -> tuple[str, str]:
    check = await db.insert_eligibility_check(org_id, {
        "appointment_id": None, "patient_name": patient_name,
        "patient_member_id": member_id,
        "payer_id": payer["id"] if payer else None,
        "payer_name": payer["name"] if payer else None,
        "is_emergency": True, "status": "pending",
    })
    await orchestrator.handle_eligibility(check["id"], {"type": "emergency_patient_registered"})
    await orchestrator.drain_detached()  # let the fire-and-forget task finish (seed only)
    row = svc_get("eligibility_checks", {"id": f"eq.{check['id']}", "select": "status"})[0]
    return check["id"], row["status"]


async def recheck(org_id: str, prior_check_id: str, *, member_id: str, emergency: bool) -> tuple[str, str]:
    prior = svc_get("eligibility_checks", {"id": f"eq.{prior_check_id}", "select": "*"})[0]
    new = await db.insert_eligibility_check(org_id, {
        "appointment_id": prior.get("appointment_id"), "previous_check_id": prior_check_id,
        "patient_name": prior["patient_name"], "patient_member_id": member_id,
        "payer_id": prior.get("payer_id"), "payer_name": prior.get("payer_name"),
        "is_emergency": prior.get("is_emergency", False), "status": "pending",
    })
    trig = "emergency_patient_registered" if emergency else "appointment_scheduled"
    await orchestrator.handle_eligibility(new["id"], {"type": trig})
    await orchestrator.drain_detached()
    row = svc_get("eligibility_checks", {"id": f"eq.{new['id']}", "select": "status"})[0]
    return new["id"], row["status"]


def wipe(org_id: str) -> None:
    svc_write("DELETE", "eligibility_checks", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "appointments", {"organization_id": f"eq.{org_id}"}, None)
    # clear only the Phase 2 audit rows (leave claims activity/escalations intact)
    svc_write("DELETE", "activity_log",
              {"organization_id": f"eq.{org_id}", "eligibility_check_id": "not.is.null"}, None)
    svc_write("DELETE", "escalations",
              {"organization_id": f"eq.{org_id}", "eligibility_check_id": "not.is.null"}, None)


SEED_ORGS = [
    ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
    ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
]


async def seed_org(email: str, org_name: str, n_sched: int, *, with_demos: bool) -> dict:
    org_id = ensure_org(email, org_name)
    wipe(org_id)
    payers = ensure_payers(org_id)
    weighted = [payers[n] for n, w in PAYER_WEIGHTS.items() for _ in range(w)]
    rng = random.Random(f"{org_id}:elig")
    now = datetime.now(timezone.utc)

    statuses: list[str] = []
    for i in range(n_sched):
        payer = rng.choice(weighted)
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        member_id = f"{rng.choice('ABMPX')}{rng.randint(10**8, 10**9 - 1)}"
        sched = (now + timedelta(days=rng.choice([2, 4, 7, 10, 14, 21]))).isoformat()
        statuses.append(await drive_scheduled(org_id, payer, name, member_id, sched))

    demos: dict[str, str] = {}
    if with_demos:
        mer, cas, smt = payers["Meridian Health Plan"], payers["Cascade Medicaid"], payers["Summit Commercial"]
        demos["SCHED-ACTIVE"] = await drive_scheduled(
            org_id, mer, "Demo Active", member_id_for_band(rng, mer["id"], True),
            (now + timedelta(days=3)).isoformat())
        demos["SCHED-INACTIVE"] = await drive_scheduled(
            org_id, cas, "Demo Inactive", member_id_for_band(rng, cas["id"], False),
            (now + timedelta(days=5)).isoformat())
        demos["SCHED-OFFLINE-PAYER"] = await drive_scheduled(
            org_id, smt, "Demo Offline Payer", member_id_for_band(rng, smt["id"], True),
            (now + timedelta(days=6)).isoformat())
        demos["SCHED-NO-CARD"] = await drive_scheduled(
            org_id, mer, "Demo No Card", "", (now + timedelta(days=8)).isoformat())

        # the EMERGENCY progression: unidentified -> insufficient_info -> (id added) -> verified_active
        er_check, er_status1 = await drive_emergency(org_id, "Unidentified Trauma", "", mer)
        good_id = member_id_for_band(rng, mer["id"], True)
        er_check2, er_status2 = await recheck(org_id, er_check, member_id=good_id, emergency=True)
        demos["ER-INITIAL"] = er_status1
        demos["ER-AFTER-RECHECK"] = er_status2

    return {"org_id": org_id, "org_name": org_name, "email": email,
            "sched_statuses": statuses, "demos": demos}


def report(res: dict) -> None:
    org_id, org_name = res["org_id"], res["org_name"]
    checks = svc_get("eligibility_checks", {"organization_id": f"eq.{org_id}",
                                            "select": "status,is_emergency,appointment_id"})
    total = len(checks)
    dist: dict[str, int] = {}
    for c in checks:
        dist[c["status"]] = dist.get(c["status"], 0) + 1

    print(f"\n  {org_name} — {total} eligibility checks "
          f"({sum(1 for c in checks if c['is_emergency'])} emergency, "
          f"{sum(1 for c in checks if not c['appointment_id'])} unscheduled)")
    print("  status distribution (simulated by 01-eligibility, deterministic):")
    for k in ("verified_active", "verified_inactive", "insufficient_info", "check_failed", "pending"):
        if dist.get(k):
            pct = 100 * dist[k] / total
            print(f"    {k:<20} {dist[k]:>3}  {pct:5.1f}%  {'#' * round(pct / 3)}")
    esc = svc_get("escalations", {"organization_id": f"eq.{org_id}",
                                  "eligibility_check_id": "not.is.null", "select": "reason_code"})
    print(f"  eligibility escalations (scheduled check_failed only): {len(esc)} "
          f"{[e['reason_code'] for e in esc]}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=int, default=14)
    ap.add_argument("--b", type=int, default=6)
    args = ap.parse_args()

    print("Foresight — seeding appointments + eligibility checks")
    print(f"  Supabase : {API}")
    print("  01 eligibility: deterministic simulation (no API key needed)\n")

    a = await seed_org(*SEED_ORGS[0], args.a, with_demos=True)
    b = await seed_org(*SEED_ORGS[1], args.b, with_demos=False)

    print("=" * 64)
    report(a)
    report(b)

    print("\n" + "=" * 64)
    print("Demo outcomes (clinic A):")
    for k, v in a["demos"].items():
        print(f"    {k:<20} {v}")

    d = a["demos"]
    ok = (
        d.get("SCHED-OFFLINE-PAYER") == "check_failed"
        and d.get("SCHED-NO-CARD") == "insufficient_info"
        and d.get("ER-INITIAL") == "insufficient_info"
        and d.get("ER-AFTER-RECHECK") == "verified_active"
    )
    print()
    print("  " + ("✓" if ok else "!") +
          " emergency progression: insufficient_info -> (member id added) -> "
          f"{d.get('ER-AFTER-RECHECK')}")
    if not ok:
        print("  ! one or more demo outcomes did not match expectations")

    print("\nLog in to review at /appointments:")
    for email, _ in SEED_ORGS:
        print(f"    {email}  /  {SEED_PW}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
