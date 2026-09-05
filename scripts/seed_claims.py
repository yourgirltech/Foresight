#!/usr/bin/env python3
"""Seed synthetic claims for the Foresight claims & billing module (Phase 1).

What it does:
  1. Creates (or reuses) two seed clinics through the real signup / bootstrap
     path, and wipes any prior seed claims so re-runs are clean.
  2. Creates a handful of payers per clinic with varied rule-engine config.
  3. Generates synthetic claims with a genuine RANDOM spread of evidence
     (authorization / documentation / coding / follow-up age) — no field is
     hardcoded to "always pass".
  4. Adds five DETERMINISTIC demo claims that each drive one Commander path,
     including an approved `resubmit_corrected_coding` -> `manual_action_required`.
  5. Runs every claim through the real agent pipeline (00 -> 06 -> 07 -> 08 -> ...)
     by calling the orchestrator directly, then approves/declines the demo
     claims.
  6. Prints the REAL risk-level distribution (written by 06) and the claim-status
     breakdown.

07-reasoning-agent calls the Claude API. Without ANTHROPIC_API_KEY the pipeline
still runs 06 (so the risk distribution is real) but claims with issues escalate
at the reasoning step instead of reaching `awaiting_approval`. The script says so.

Usage:
    python scripts/seed_claims.py                 # default 30 + 12 random claims
    python scripts/seed_claims.py --a 40 --b 8
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

# Windows consoles default to cp1252; the report below uses — and ✓.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------- #
# config — resolve from env or `supabase status`, then hand it to the backend
# --------------------------------------------------------------------------- #
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
# hand config to the backend settings (config.py reads these env vars)
os.environ.setdefault("SUPABASE_URL", CFG["api_url"])
os.environ.setdefault("SUPABASE_ANON_KEY", CFG["anon_key"] or "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", CFG["service_role_key"])
os.environ.setdefault("SUPABASE_JWT_SECRET", CFG["jwt_secret"] or "")

sys.path.insert(0, str(BACKEND))
from app.agents import db, orchestrator  # noqa: E402
from app.config import get_settings  # noqa: E402

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]


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
# org bootstrap (real signup path, idempotent)
# --------------------------------------------------------------------------- #
SEED_PW = "Foresight-seed-123!"


def ensure_org(email: str, org_name: str) -> tuple[str, str]:
    """Return (organization_id, admin_user_id). Reuse if the org already exists."""
    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin_headers())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}

    if existing:
        org_id = existing[0]["id"]
        uid = by_email.get(email)
        if uid is None:
            uid = _create_user(email)
            _bootstrap_existing_profile(uid, org_id)
        return org_id, uid

    uid = by_email.get(email) or _create_user(email)
    token = _sign_in(email)
    st, body = _http(
        "POST", f"{REST}/rpc/bootstrap_organization",
        {"apikey": ANON, "Authorization": f"Bearer {token}"}, {"org_name": org_name},
    )
    if st != 200:
        sys.exit(f"bootstrap_organization failed for {org_name}: {st} {body}")
    return body["id"], uid


def _create_user(email: str) -> str:
    st, body = _http(
        "POST", f"{AUTH}/admin/users", _admin_headers(),
        {"email": email, "password": SEED_PW, "email_confirm": True},
    )
    if st not in (200, 201):
        sys.exit(f"failed to create seed user {email}: {st} {body}")
    return body["id"]


def _sign_in(email: str) -> str:
    st, body = _http(
        "POST", f"{AUTH}/token?grant_type=password",
        {"apikey": ANON}, {"email": email, "password": SEED_PW},
    )
    if st != 200:
        sys.exit(f"failed to sign in seed user {email}: {st} {body}")
    return body["access_token"]


def _bootstrap_existing_profile(uid: str, org_id: str) -> None:
    svc_write("PATCH", "profiles", {"id": f"eq.{uid}"},
              {"organization_id": org_id, "role": "clinic_admin"})


# --------------------------------------------------------------------------- #
# payers + claim generation
# --------------------------------------------------------------------------- #
PAYER_SPECS = [
    ("Meridian Health Plan", True, True, 21),
    ("BlueRidge PPO", True, False, 30),
    ("Cascade Medicaid", False, True, 14),
    ("Summit Commercial", False, False, None),
]

FIRST = ["Ava", "Liam", "Noah", "Mia", "Ella", "Owen", "Lucas", "Aria", "Leo", "Nora",
         "Kai", "Zoe", "Ivy", "Jude", "Cora", "Max", "Ruby", "Eli", "June", "Sam"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Silva", "Okafor", "Brooks",
        "Reyes", "Haddad", "Weber", "Flores", "Cohen", "Bauer", "Ford", "Mercer", "Ali"]


# payer names by how often the generator picks them (realistic mix + enough
# claims on the "requires auth AND docs" payer to populate the High band)
PAYER_WEIGHTS = {
    "Meridian Health Plan": 3,
    "BlueRidge PPO": 2,
    "Cascade Medicaid": 2,
    "Summit Commercial": 1,
}


def rand_claim(rng: random.Random, seq: int, payer: dict) -> dict:
    """Independent evidence rolls — tuned so Low / Medium / High are all well
    represented once 06 scores them. Nothing is hardcoded to pass."""
    now = datetime.now(timezone.utc)
    age_days = rng.choice([3, 6, 10, 14, 20, 28, 40, 55, 75])
    created = now - timedelta(days=age_days)
    followed_up = rng.random() < 0.4
    return {
        "claim_id": f"CLM-{now.year}-{seq:05d}",
        "patient_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
        "patient_member_id": f"{rng.choice('ABMPX')}{rng.randint(10**8, 10**9 - 1)}",
        "amount": round(rng.uniform(80, 4200), 2),
        "authorization_present": rng.random() < 0.55,
        "documentation_present": rng.random() < 0.58,
        "coding_matches": rng.random() < 0.62,
        "last_followup_at": (now - timedelta(days=rng.randint(1, age_days)))
        .isoformat() if followed_up else None,
        "created_at": created.isoformat(),
        "payer_id": payer["id"],
        "organization_id": payer["organization_id"],
    }


def demo_claims(org_id: str, payers: dict[str, dict], year: int) -> list[dict]:
    """Five deterministic claims, one per Commander path."""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=90)).isoformat()
    mer, summit, cascade = payers["Meridian Health Plan"], payers["Summit Commercial"], payers["Cascade Medicaid"]

    def base(cid, payer, **over):
        d = {
            "claim_id": cid, "organization_id": org_id, "payer_id": payer["id"],
            "patient_name": "Demo Patient", "patient_member_id": "DEMO-000",
            "amount": 1234.00, "authorization_present": True, "documentation_present": True,
            "coding_matches": True, "last_followup_at": now.isoformat(),
            "created_at": now.isoformat(),
        }
        d.update(over)
        return d

    return [
        # code_mismatch only, payer needs neither auth nor docs -> resubmit_corrected_coding, High conf
        base(f"CLM-{year}-DEMO-CODING", summit, patient_name="Demo Coding", coding_matches=False),
        # missing auth only -> submit_authorization_request -> agent-executed (09)
        base(f"CLM-{year}-DEMO-AUTH", mer, patient_name="Demo Auth", authorization_present=False),
        # overdue only -> payer_status_follow_up -> agent-executed (10)
        base(f"CLM-{year}-DEMO-FOLLOWUP", cascade, patient_name="Demo Followup",
             documentation_present=True, last_followup_at=None, created_at=old),
        # auth + doc + code -> 3 issues -> Low confidence -> R13 escalation (never offered for approval)
        base(f"CLM-{year}-DEMO-COMPOUND", mer, patient_name="Demo Compound",
             authorization_present=False, documentation_present=False, coding_matches=False),
        # missing doc -> request_documentation -> declined by a human
        base(f"CLM-{year}-DEMO-DECLINE", cascade, patient_name="Demo Decline",
             documentation_present=False),
    ]


# --------------------------------------------------------------------------- #
# pipeline driving
# --------------------------------------------------------------------------- #
async def drive_ingest(claim_pk: str) -> str:
    d = await orchestrator.handle(claim_pk, {"type": "claim.ingested"})
    return d.reason_code


async def drive_decision(org_id: str, claim_pk: str, admin_uid: str, approve: bool) -> str | None:
    rec = await db.latest_recommendation(org_id, claim_pk)
    if not rec or rec["approval_status"] != "pending":
        return None
    await db.set_recommendation_decision(
        org_id, rec["id"],
        approval_status="approved" if approve else "declined",
        decided_by=admin_uid,
    )
    d = await orchestrator.handle(
        claim_pk,
        {"type": "human.approved" if approve else "human.declined",
         "payload": {"user_id": admin_uid, "recommendation_id": rec["id"]}},
    )
    return d.reason_code


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
SEED_ORGS = [
    ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
    ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
]


def wipe(org_id: str) -> None:
    svc_write("DELETE", "claims", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "payers", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "activity_log", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "escalations", {"organization_id": f"eq.{org_id}"}, None)


def make_payers(org_id: str) -> dict[str, dict]:
    out = {}
    for name, auth_req, doc_req, thr in PAYER_SPECS:
        row = svc_write("POST", "payers", {}, {
            "organization_id": org_id, "name": name,
            "authorization_required": auth_req, "documentation_required": doc_req,
            "follow_up_threshold_days": thr,
        })[0]
        out[name] = row
    return out


async def seed_org(email: str, org_name: str, n_random: int, *, with_demos: bool) -> dict:
    org_id, admin_uid = ensure_org(email, org_name)
    wipe(org_id)
    payers = make_payers(org_id)
    weighted = [payers[name] for name, w in PAYER_WEIGHTS.items() for _ in range(w)]
    year = datetime.now(timezone.utc).year
    rng = random.Random(f"{org_id}:{n_random}")

    rows = [rand_claim(rng, i + 1, rng.choice(weighted)) for i in range(n_random)]
    if with_demos:
        rows += demo_claims(org_id, payers, year)

    created = svc_write("POST", "claims", {}, rows)
    print(f"  {org_name}: inserted {len(created)} claims, {len(payers)} payers")

    by_cid = {c["claim_id"]: c["id"] for c in created}
    for c in created:
        await drive_ingest(c["id"])

    demo_outcomes = {}
    if with_demos:
        demo_outcomes["CODING"] = await drive_decision(org_id, by_cid[f"CLM-{year}-DEMO-CODING"], admin_uid, approve=True)
        demo_outcomes["AUTH"] = await drive_decision(org_id, by_cid[f"CLM-{year}-DEMO-AUTH"], admin_uid, approve=True)
        demo_outcomes["FOLLOWUP"] = await drive_decision(org_id, by_cid[f"CLM-{year}-DEMO-FOLLOWUP"], admin_uid, approve=True)
        demo_outcomes["DECLINE"] = await drive_decision(org_id, by_cid[f"CLM-{year}-DEMO-DECLINE"], admin_uid, approve=False)

    return {"org_id": org_id, "org_name": org_name, "email": email, "demo_outcomes": demo_outcomes}


def report(org_id: str, org_name: str) -> None:
    claims = svc_get("claims", {"organization_id": f"eq.{org_id}",
                                "select": "risk_level,risk_score,status,claim_id"})
    total = len(claims)
    risk = {"Low": 0, "Medium": 0, "High": 0, "unscored": 0}
    status: dict[str, int] = {}
    for c in claims:
        risk[c["risk_level"] or "unscored"] = risk.get(c["risk_level"] or "unscored", 0) + 1
        status[c["status"]] = status.get(c["status"], 0) + 1

    print(f"\n  {org_name} — {total} claims")
    print("  risk level (written by 06-analyzer):")
    for lvl in ("Low", "Medium", "High", "unscored"):
        if risk.get(lvl):
            pct = 100 * risk[lvl] / total
            bar = "#" * round(pct / 3)
            print(f"    {lvl:<9} {risk[lvl]:>3}  {pct:5.1f}%  {bar}")
    print("  claim status:")
    for k in sorted(status):
        print(f"    {k:<24} {status[k]:>3}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=int, default=30, help="random claims for clinic A")
    ap.add_argument("--b", type=int, default=12, help="random claims for clinic B")
    args = ap.parse_args()

    key_present = bool(get_settings().anthropic_api_key)
    print("Foresight — seeding claims")
    print(f"  Supabase : {API}")
    print(f"  07 reasoning: {'ANTHROPIC_API_KEY present' if key_present else 'NO KEY — claims with issues will escalate at the reasoning step'}")
    print()

    a = await seed_org(*SEED_ORGS[0], args.a, with_demos=True)
    b = await seed_org(*SEED_ORGS[1], args.b, with_demos=False)

    print("\n" + "=" * 64)
    report(a["org_id"], a["org_name"])
    report(b["org_id"], b["org_name"])

    print("\n" + "=" * 64)
    print("Demo claim outcomes (clinic A):")
    for k, v in a["demo_outcomes"].items():
        print(f"    CLM-*-DEMO-{k:<9} commander reason_code: {v}")

    year = datetime.now(timezone.utc).year
    manual = svc_get("claims", {
        "organization_id": f"eq.{a['org_id']}",
        "claim_id": f"eq.CLM-{year}-DEMO-CODING", "select": "status",
    })
    manual_status = manual[0]["status"] if manual else "?"
    print()
    if manual_status == "manual_action_required":
        print("  ✓ CLM-*-DEMO-CODING is at manual_action_required — approved resubmit_corrected_coding.")
    else:
        print(f"  ! CLM-*-DEMO-CODING is at '{manual_status}', not manual_action_required.")
        if not key_present:
            print("    Set ANTHROPIC_API_KEY and re-run to complete the reasoning->recommendation->approval path.")

    print("\nLog in to the UI to review:")
    for email, _ in SEED_ORGS:
        print(f"    {email}  /  {SEED_PW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
