#!/usr/bin/env python3
"""Seed appeals for the Foresight appeals module (Phase 5 / 11-appeals-agent).

What it does:
  1. Reuses the two seed clinics (real signup / bootstrap path).
  2. Wipes appeals + the appeal-tagged audit rows + the denied claims this
     script created (claim_id prefix "APL-SEED-") so re-runs are clean.
  3. Creates DENIED claims with recorded denial_reason strings across the
     grounds spectrum — some with rule-engine issues, some denial-reason-only,
     one bare (no citable basis) — and drives each through
     orchestrator.handle_appeal.
  4. Runs the demo spread:
       - a few left at `drafted` (a realistic "awaiting your approval" queue);
       - a few auto-approved -> the deterministic won / partial / upheld split;
       - one `insufficient_basis` -> escalation;
       - one `appeal_denied` -> resubmit (affirmed documentation) -> won.
  5. Prints the resolution distribution (approved / partial / denied).

Needs ANTHROPIC_API_KEY for the draft step (11 has no hollow fallback — a claim
whose draft can't be reached is recorded `error`). The pure parts run without it.

Usage:
    python scripts/seed_appeals.py                 # default 10 (A) + 6 (B)
    python scripts/seed_appeals.py --a 14 --b 4
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

# surface ANTHROPIC_API_KEY from backend/.env for the draft step
for _k, _v in _dotenv(BACKEND / ".env").items():
    if _k == "ANTHROPIC_API_KEY" and _v:
        os.environ.setdefault("ANTHROPIC_API_KEY", _v.strip('"').strip("'"))

sys.path.insert(0, str(BACKEND))
from app.agents import db, orchestrator  # noqa: E402
from app.agents.appeals import (  # noqa: E402
    APPEAL_PARTIAL_BAND,
    APPEAL_RESUBMIT_BONUS,
    resolution_bucket,
    win_threshold,
)
from app.config import get_settings  # noqa: E402

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"
CLAIM_PREFIX = "APL-SEED-"
NOW = datetime.now(timezone.utc)


def _admin_headers():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def svc_get(path, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return _http("GET", f"{REST}/{path}?{q}", _admin_headers())[1] or []


def svc_write(method, path, params, body):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**_admin_headers(), "Prefer": "return=representation"}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org(email: str, org_name: str) -> tuple[str, str]:
    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin_headers())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}
    uid = by_email.get(email)
    if existing:
        org_id = existing[0]["id"]
        if uid is None:
            st, body = _http("POST", f"{AUTH}/admin/users", _admin_headers(),
                             {"email": email, "password": SEED_PW, "email_confirm": True})
            uid = body["id"]
            svc_write("PATCH", "profiles", {"id": f"eq.{uid}"},
                      {"organization_id": org_id, "role": "clinic_admin"})
        return org_id, uid
    if uid is None:
        st, body = _http("POST", f"{AUTH}/admin/users", _admin_headers(),
                         {"email": email, "password": SEED_PW, "email_confirm": True})
        uid = body["id"]
    st, body = _http("POST", f"{AUTH}/token?grant_type=password",
                     {"apikey": ANON}, {"email": email, "password": SEED_PW})
    token = body["access_token"]
    st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                     {"apikey": ANON, "Authorization": f"Bearer {token}"}, {"org_name": org_name})
    if st != 200:
        sys.exit(f"bootstrap_organization failed for {org_name}: {st} {body}")
    return body["id"], uid


SEED_ORGS = [
    ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
    ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
]

FIRST = ["Ava", "Liam", "Noah", "Mia", "Ella", "Owen", "Lucas", "Aria", "Leo", "Nora",
         "Kai", "Zoe", "Ivy", "Jude", "Cora", "Max", "Ruby", "Eli", "June", "Sam"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Silva", "Okafor", "Brooks",
        "Reyes", "Haddad", "Weber", "Flores", "Cohen", "Bauer", "Ford", "Mercer", "Ali"]

DENIAL_REASONS = [
    "Prior authorization not on file for this service.",
    "Documentation does not support the level of service billed.",
    "Service determined not medically necessary.",
    "Claim exceeds timely filing limit.",
    "Procedure considered bundled with another service on the same date.",
]
ISSUE_SPECS = {
    "missing_authorization": ("high", "Payer requires prior authorization for this service and none is recorded on the claim."),
    "code_mismatch": ("medium", "The billed procedure code is higher than the attached documentation supports."),
    "missing_documentation": ("high", "No operative or progress note is attached for the billed procedure."),
}
PAYER_NAMES = ["Meridian Health Plan", "BlueRidge PPO", "Cascade Medicaid", "Summit Commercial"]


def ensure_payers(org_id: str) -> list[dict]:
    have = {p["name"]: p for p in svc_get("payers", {"organization_id": f"eq.{org_id}", "select": "*"})}
    out = []
    for name in PAYER_NAMES:
        if name in have:
            out.append(have[name])
        else:
            out.append(svc_write("POST", "payers", {}, {
                "organization_id": org_id, "name": name,
                "authorization_required": True, "documentation_required": True,
                "follow_up_threshold_days": 21})[0])
    return out


def wipe(org_id: str) -> None:
    claims = svc_get("claims", {"organization_id": f"eq.{org_id}",
                                "claim_id": f"like.{CLAIM_PREFIX}*", "select": "id"})
    ids = [c["id"] for c in claims]
    for cid in ids:
        svc_write("DELETE", "appeals", {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{cid}"}, None)
        svc_write("DELETE", "activity_log", {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{cid}"}, None)
        svc_write("DELETE", "escalations", {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{cid}"}, None)
        svc_write("DELETE", "claim_issues", {"organization_id": f"eq.{org_id}", "claim_id": f"eq.{cid}"}, None)
    if ids:
        svc_write("DELETE", "claims",
                  {"organization_id": f"eq.{org_id}", "claim_id": f"like.{CLAIM_PREFIX}*"}, None)


def make_denied_claim(org_id: str, payer: dict, seq: int, rng: random.Random, *,
                      denial_reason: str | None, issue_types: list[str], amount: float | None = None) -> dict:
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    claim = svc_write("POST", "claims", {}, {
        "organization_id": org_id, "payer_id": payer["id"],
        "claim_id": f"{CLAIM_PREFIX}{seq:04d}",
        "patient_name": name, "patient_member_id": f"{rng.choice('ABMPX')}{rng.randint(10**7, 10**8)}",
        "amount": amount if amount is not None else float(rng.choice([420, 780, 1150, 1900, 2650, 3400])),
        "status": "denied", "denial_reason": denial_reason,
        "authorization_present": "missing_authorization" not in issue_types,
        "documentation_present": "missing_documentation" not in issue_types,
        "coding_matches": "code_mismatch" not in issue_types,
        "created_at": (NOW - timedelta(days=rng.randint(15, 60))).isoformat(),
    })[0]
    for it in issue_types:
        sev, desc = ISSUE_SPECS[it]
        svc_write("POST", "claim_issues", {}, {
            "organization_id": org_id, "claim_id": claim["id"],
            "issue_type": it, "severity": sev, "description": desc})
    return claim


def distinct_grounds(issue_types: list[str], denial_reason: str | None) -> int:
    return len(set(issue_types)) + (1 if denial_reason else 0)


def appeal_id_for_band(claim_pk: str, dg: int, band: str, rng: random.Random, *, is_resubmit=False) -> str:
    import uuid as _uuid
    win = win_threshold(dg)
    for _ in range(200000):
        cand = str(_uuid.uuid4())
        raw = resolution_bucket(claim_pk, cand)
        eff = max(0, raw - APPEAL_RESUBMIT_BONUS) if is_resubmit else raw
        if band == "approved" and eff < win - 2:
            return cand
        if band == "partial" and win + 1 <= eff < win + APPEAL_PARTIAL_BAND - 1:
            return cand
        if band == "denied" and eff >= win + APPEAL_PARTIAL_BAND + 2:
            return cand
        if band == "any":
            return cand
    raise RuntimeError(f"no appeal id for band {band}")


async def draft_via_resubmit(org_id: str, claim_pk: str, appeal_id: str, denial_reason: str | None) -> None:
    await db.insert_appeal(org_id, {"id": appeal_id, "claim_id": claim_pk, "status": "pending",
                                    "denial_reason": denial_reason})
    await orchestrator.handle_appeal(claim_pk, {"type": "appeal_resubmitted"})


async def approve(org_id: str, claim_pk: str, appeal_id: str, uid: str) -> None:
    await db.update_appeal(org_id, appeal_id, {"reviewed_by": uid, "reviewed_at": NOW.isoformat()})
    await orchestrator.handle_appeal(claim_pk, {"type": "appeal_submission_approved"})


async def seed_org(email: str, org_name: str, n: int, uid_hint: str, *, with_demos: bool) -> dict:
    org_id, uid = ensure_org(email, org_name)
    wipe(org_id)
    payers = ensure_payers(org_id)
    rng = random.Random(f"{org_id}:appeals")
    key = bool(get_settings().anthropic_api_key)

    seq = 1
    outcomes = {"approved": 0, "partial": 0, "denied": 0}
    left_drafted = 0
    errors = 0

    # a spread of denied claims: ~half issues+reason, ~third reason-only, a couple bare
    for i in range(n):
        r = rng.random()
        if r < 0.5:
            its = rng.sample(list(ISSUE_SPECS), rng.choice([1, 2]))
            dr = rng.choice(DENIAL_REASONS)
        elif r < 0.85:
            its = []
            dr = rng.choice(DENIAL_REASONS)
        else:
            its = []
            dr = None
        claim = make_denied_claim(org_id, rng.choice(payers), seq, rng, denial_reason=dr, issue_types=its)
        seq += 1
        dg = distinct_grounds(its, dr)

        if dg == 0:
            await orchestrator.handle_appeal(claim["id"], {"type": "claim_denied"})
            continue

        auto = rng.random() > 0.45  # leave ~45% on the approval queue
        if not key:
            await orchestrator.handle_appeal(claim["id"], {"type": "claim_denied"})
            errors += 1
            continue

        band = rng.choices(["approved", "partial", "denied"], weights=[6, 2, 3])[0] if auto else "any"
        aid = appeal_id_for_band(claim["id"], dg, band, rng)
        await draft_via_resubmit(org_id, claim["id"], aid, dr)
        if not auto:
            left_drafted += 1
            continue
        await approve(org_id, claim["id"], aid, uid)
        res = svc_get("appeals", {"id": f"eq.{aid}", "select": "resolution_payload"})[0]
        outcomes[res["resolution_payload"].get("outcome", "denied")] += 1

    demos: dict[str, str] = {}
    if with_demos and key:
        # a bare denied claim -> insufficient_basis -> escalation
        bare = make_denied_claim(org_id, payers[0], seq, rng, denial_reason=None, issue_types=[])
        seq += 1
        await orchestrator.handle_appeal(bare["id"], {"type": "claim_denied"})
        ba = svc_get("appeals", {"claim_id": f"eq.{bare['id']}", "select": "status"})
        demos["NO-BASIS"] = ba[0]["status"] if ba else "?"

        # an upheld appeal -> resubmit (affirmed docs) -> won
        c = make_denied_claim(org_id, payers[2], seq, rng,
                              denial_reason="Documentation does not support the level of service billed.",
                              issue_types=["missing_documentation", "code_mismatch"])
        seq += 1
        dg = 3
        first = appeal_id_for_band(c["id"], dg, "denied", rng)
        await draft_via_resubmit(org_id, c["id"], first, c["denial_reason"])
        await approve(org_id, c["id"], first, uid)
        demos["RESUBMIT-STEP-1"] = svc_get("appeals", {"id": f"eq.{first}", "select": "status"})[0]["status"]
        second = appeal_id_for_band(c["id"], dg, "approved", rng, is_resubmit=True)
        await db.insert_appeal(org_id, {"id": second, "claim_id": c["id"], "status": "pending",
                                        "previous_appeal_id": first, "denial_reason": c["denial_reason"]})
        await orchestrator.handle_appeal(c["id"], {"type": "appeal_resubmitted"})
        await approve(org_id, c["id"], second, uid)
        demos["RESUBMIT-STEP-2"] = svc_get("appeals", {"id": f"eq.{second}", "select": "status"})[0]["status"]
        demos["RESUBMIT-CLAIM"] = svc_get("claims", {"id": f"eq.{c['id']}", "select": "status"})[0]["status"]

    return {"org_id": org_id, "org_name": org_name, "email": email, "key": key,
            "outcomes": outcomes, "left_drafted": left_drafted, "errors": errors, "demos": demos}


def report(res: dict) -> None:
    org_id, org_name = res["org_id"], res["org_name"]
    appeals = svc_get("appeals", {"organization_id": f"eq.{org_id}",
                                  "select": "status,previous_appeal_id,resolution_payload"})
    superseded = {a["previous_appeal_id"] for a in appeals if a.get("previous_appeal_id")}
    total = len(appeals)
    resolved = [a for a in appeals if a["status"] in ("appeal_approved", "appeal_partial", "appeal_denied")]
    dist = {"approved": 0, "partial": 0, "denied": 0}
    for a in resolved:
        dist[a["resolution_payload"].get("outcome", "denied")] += 1
    drafted = sum(1 for a in appeals if a["status"] == "drafted")
    nobasis = sum(1 for a in appeals if a["status"] == "insufficient_basis")
    errored = sum(1 for a in appeals if a["status"] == "error")

    print(f"\n  {org_name} — {total} appeal rows ({len(superseded)} superseded by a resubmit)")
    print(f"    drafted / awaiting approval : {drafted}")
    print(f"    insufficient_basis         : {nobasis}")
    if errored:
        print(f"    error (no draft model)     : {errored}")
    rt = sum(dist.values())
    print(f"    RESOLUTION distribution (11.submit -> simulate_resolution, deterministic): {rt} resolved")
    for k in ("approved", "partial", "denied"):
        n = dist[k]
        if n:
            pct = 100 * n / rt if rt else 0
            print(f"      {k:<10} {n:>3}  {pct:5.1f}%  {'#' * round(pct / 4)}")
    esc = svc_get("escalations", {"organization_id": f"eq.{org_id}", "reason_code": "like.appeal_*",
                                  "select": "reason_code"})
    print(f"    appeal escalations: {len(esc)} {sorted({e['reason_code'] for e in esc})}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=int, default=10)
    ap.add_argument("--b", type=int, default=6)
    args = ap.parse_args()

    print("Foresight — seeding appeals (Phase 5 / 11-appeals-agent)")
    print(f"  Supabase : {API}")
    key = bool(get_settings().anthropic_api_key)
    print(f"  draft step: {'ANTHROPIC_API_KEY present' if key else 'NO KEY — appeals will record `error` (11 has no fallback)'}\n")

    a = await seed_org(*SEED_ORGS[0], args.a, "a", with_demos=True)
    b = await seed_org(*SEED_ORGS[1], args.b, "b", with_demos=False)

    print("=" * 64)
    report(a)
    report(b)

    if a["demos"]:
        print("\n" + "=" * 64)
        print("Demo outcomes (clinic A):")
        for k, v in a["demos"].items():
            print(f"    {k:<18} {v}")

    ok = True
    if key:
        d = a["demos"]
        ok = (d.get("NO-BASIS") == "insufficient_basis"
              and d.get("RESUBMIT-STEP-1") == "appeal_denied"
              and d.get("RESUBMIT-STEP-2") == "appeal_approved"
              and d.get("RESUBMIT-CLAIM") == "paid")
        print("\n  " + ("OK  " if ok else "!   ") +
              "no-basis -> escalation; upheld -> resubmit (bonus) -> won -> claim paid")

    print("\nLog in to review a denied claim at /app/claims:")
    for email, _ in SEED_ORGS:
        print(f"    {email}  /  {SEED_PW}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
