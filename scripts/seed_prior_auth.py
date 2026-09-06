#!/usr/bin/env python3
"""Seed prior authorizations for the Foresight prior-auth module (Phase 3).

What it does:
  1. Reuses the two seed clinics (real signup / bootstrap path).
  2. Ensures the four seed payers exist and sets their prior-auth knobs
     (docs/agents/02-prior-auth-agent.md §7.7) — a documented spread so the
     determination + response distributions are reproducible.
  3. Wipes prior_authorizations + the PA-tagged audit rows + the appointments
     this script created (name prefix "PA-Seed:") so re-runs are clean.
  4. Generates appointments + prior-authorization rows with a seeded RNG
     (deterministic procedure codes + member ids -> deterministic determination
     and, after an auto-approval, deterministic payer response), and drives each
     through orchestrator.handle_prior_auth.
  5. Runs demo scenarios: an always-auth procedure -> required_draft -> approve
     -> response; a routine procedure -> not_required; a blank code ->
     insufficient_info; an EMERGENCY service -> emergency_exempt (detached);
     an info_needed -> resubmit -> approved chain.
  6. Prints BOTH distributions: determination (required_draft / not_required /
     insufficient_info / emergency_exempt) and payer response
     (approved / info_needed / denied).

No ANTHROPIC_API_KEY needed — 02 has no LLM step.

Usage:
    python scripts/seed_prior_auth.py                  # default 18 + 8 PAs
    python scripts/seed_prior_auth.py --a 24 --b 6
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
from app.agents.prior_auth import (  # noqa: E402
    ALWAYS_AUTH_PROCEDURES,
    ELECTIVE_AUTH_PROCEDURES,
    INFO_NEEDED_BAND,
    response_bucket,
)

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"
NAME_PREFIX = "PA-Seed:"


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
# org bootstrap (real signup path, idempotent) — same as seed_eligibility.py
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
# payers — the prior-auth simulation knobs (02-prior-auth-agent.md §7.7)
# --------------------------------------------------------------------------- #
#   name                 : (pa_supported, pa_required_default, pa_approval_threshold)
PAYER_SPECS = {
    "Meridian Health Plan": (True, False, 88),   # commercial; auth only for the always-auth set
    "BlueRidge PPO":        (True, True, 72),    # PPO; auth for elective set too; middling approval
    "Cascade Medicaid":     (True, True, 60),    # Medicaid; broad requirements; more denials/info
    "Summit Commercial":    (False, True, 78),   # no electronic PA channel -> manual_fax
}
PAYER_WEIGHTS = {"Meridian Health Plan": 4, "BlueRidge PPO": 3, "Cascade Medicaid": 3, "Summit Commercial": 1}

# routine procedures that never need auth (illustrative)
NO_AUTH_PROCEDURES = ["99213", "99214", "36415", "80053", "93000", "20610", "87070"]

FIRST = ["Ava", "Liam", "Noah", "Mia", "Ella", "Owen", "Lucas", "Aria", "Leo", "Nora",
         "Kai", "Zoe", "Ivy", "Jude", "Cora", "Max", "Ruby", "Eli", "June", "Sam"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Silva", "Okafor", "Brooks",
        "Reyes", "Haddad", "Weber", "Flores", "Cohen", "Bauer", "Ford", "Mercer", "Ali"]
PROC_DESC = {
    "70553": "MRI brain with and without contrast", "72148": "MRI lumbar spine without contrast",
    "72141": "MRI cervical spine without contrast", "J3489": "Zoledronic acid infusion",
    "J0178": "Aflibercept injection", "97110-EXT": "Extended physical therapy course",
    "43239-SURG": "Elective upper GI surgical bundle", "29881": "Knee arthroscopy with meniscectomy",
    "29827": "Shoulder arthroscopy with rotator cuff repair", "62323": "Lumbar transforaminal epidural injection",
    "64483": "Transforaminal epidural injection, single level", "95810": "Attended polysomnography",
    "99213": "Office visit, established patient", "99214": "Office visit, established patient (moderate)",
    "36415": "Routine venipuncture", "80053": "Comprehensive metabolic panel",
    "93000": "Electrocardiogram, complete", "20610": "Joint aspiration/injection, major",
    "87070": "Bacterial culture",
}


def ensure_payers(org_id: str) -> dict[str, dict]:
    have = {p["name"]: p for p in svc_get("payers", {"organization_id": f"eq.{org_id}", "select": "*"})}
    out: dict[str, dict] = {}
    for name, (supported, required_default, threshold) in PAYER_SPECS.items():
        fields = {
            "prior_auth_supported": supported,
            "prior_auth_required_default": required_default,
            "prior_auth_approval_threshold": threshold,
        }
        if name in have:
            out[name] = svc_write("PATCH", "payers",
                                  {"organization_id": f"eq.{org_id}", "id": f"eq.{have[name]['id']}"},
                                  fields)[0]
        else:
            # a payer that didn't exist yet needs its Phase-1 columns too
            base = {"authorization_required": False, "documentation_required": False}
            out[name] = svc_write("POST", "payers", {},
                                  {"organization_id": org_id, "name": name, **base, **fields})[0]
    return out


def member_for_response_band(rng: random.Random, payer_id: str, procedure_code: str,
                             threshold: int, band: str) -> str:
    """Deterministically pick a member id whose response bucket lands in the
    wanted band for this (payer, procedure). Documented + reproducible."""
    lo_info, hi_info = threshold, threshold + INFO_NEEDED_BAND
    mid = ""
    for _ in range(2000):
        mid = f"{rng.choice('ABMPX')}{rng.randint(10**8, 10**9 - 1)}"
        b = response_bucket(mid, payer_id, procedure_code)
        if band == "approved" and b < lo_info - 5:
            return mid
        if band == "info_needed" and lo_info <= b < hi_info:
            return mid
        if band == "denied" and b >= hi_info + 3:
            return mid
    return mid  # fallback (shouldn't happen)


# --------------------------------------------------------------------------- #
# driving
# --------------------------------------------------------------------------- #
async def _make_appointment(org_id: str, name: str, member_id: str, payer_id: str | None,
                            *, emergency: bool, when_days: int) -> str | None:
    if emergency:
        return None
    appt = await db.insert_appointment(org_id, {
        "patient_name": f"{NAME_PREFIX} {name}", "patient_member_id": member_id,
        "payer_id": payer_id,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=when_days)).isoformat(),
        "is_emergency": False,
    })
    return appt["id"]


async def drive_pa(org_id: str, payer: dict | None, name: str, member_id: str,
                   procedure_code: str, *, emergency: bool, place: str = "office",
                   auto_approve: bool = True, when_days: int = 7) -> dict:
    """Create an appointment + a PA row, run the determination, and (for a
    required_draft, non-emergency row when auto_approve) simulate a human
    approving the submission so the payer response is exercised."""
    appt_id = await _make_appointment(org_id, name, member_id,
                                      payer["id"] if payer else None,
                                      emergency=emergency, when_days=when_days)
    pa = await db.insert_prior_authorization(org_id, {
        "appointment_id": appt_id,
        "patient_name": f"{NAME_PREFIX} {name}",
        "patient_member_id": member_id,
        "payer_id": payer["id"] if payer else None,
        "payer_name": payer["name"] if payer else None,
        "procedure_code": procedure_code,
        "procedure_description": PROC_DESC.get(procedure_code, ""),
        "place_of_service": "emergency" if emergency else place,
        "is_emergency": emergency,
        "status": "pending",
    })
    trig = "prior_auth_emergency" if emergency else "prior_auth_requested"
    await orchestrator.handle_prior_auth(pa["id"], {"type": trig})
    await orchestrator.drain_detached()

    row = svc_get("prior_authorizations", {"id": f"eq.{pa['id']}", "select": "*"})[0]
    if auto_approve and not emergency and row["status"] == "required_draft":
        await db.update_prior_authorization(org_id, pa["id"], {"decided_by": None})
        await orchestrator.handle_prior_auth(pa["id"], {"type": "prior_auth_submission_approved"})
        await orchestrator.drain_detached()
        row = svc_get("prior_authorizations", {"id": f"eq.{pa['id']}", "select": "*"})[0]
    return row


async def resubmit(org_id: str, prior_pa_id: str) -> dict:
    prior = svc_get("prior_authorizations", {"id": f"eq.{prior_pa_id}", "select": "*"})[0]
    new = await db.insert_prior_authorization(org_id, {
        "appointment_id": prior.get("appointment_id"),
        "previous_auth_id": prior_pa_id,
        "patient_name": prior["patient_name"],
        "patient_member_id": prior.get("patient_member_id", ""),
        "payer_id": prior.get("payer_id"),
        "payer_name": prior.get("payer_name"),
        "procedure_code": prior.get("procedure_code", ""),
        "procedure_description": prior.get("procedure_description", ""),
        "place_of_service": prior.get("place_of_service", "office"),
        "is_emergency": prior.get("is_emergency", False),
        "status": "pending",
    })
    await orchestrator.handle_prior_auth(new["id"], {"type": "prior_auth_requested"})
    await orchestrator.drain_detached()
    row = svc_get("prior_authorizations", {"id": f"eq.{new['id']}", "select": "*"})[0]
    if row["status"] == "required_draft":
        await orchestrator.handle_prior_auth(new["id"], {"type": "prior_auth_submission_approved"})
        await orchestrator.drain_detached()
        row = svc_get("prior_authorizations", {"id": f"eq.{new['id']}", "select": "*"})[0]
    return row


def wipe(org_id: str) -> None:
    svc_write("DELETE", "prior_authorizations", {"organization_id": f"eq.{org_id}"}, None)
    svc_write("DELETE", "appointments",
              {"organization_id": f"eq.{org_id}", "patient_name": f"like.{NAME_PREFIX}*"}, None)
    svc_write("DELETE", "activity_log",
              {"organization_id": f"eq.{org_id}", "prior_authorization_id": "not.is.null"}, None)
    svc_write("DELETE", "escalations",
              {"organization_id": f"eq.{org_id}", "prior_authorization_id": "not.is.null"}, None)


SEED_ORGS = [
    ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
    ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
]

ALWAYS = sorted(ALWAYS_AUTH_PROCEDURES)
ELECTIVE = sorted(ELECTIVE_AUTH_PROCEDURES)


async def seed_org(email: str, org_name: str, n: int, *, with_demos: bool) -> dict:
    org_id = ensure_org(email, org_name)
    wipe(org_id)
    payers = ensure_payers(org_id)
    weighted = [payers[nm] for nm, w in PAYER_WEIGHTS.items() for _ in range(w)]
    rng = random.Random(f"{org_id}:pa")

    # procedure pool: ~1/3 always-auth, ~1/3 elective, ~1/3 routine
    proc_pool = ALWAYS * 2 + ELECTIVE * 2 + NO_AUTH_PROCEDURES

    rows: list[dict] = []
    for i in range(n):
        payer = rng.choice(weighted)
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        member_id = f"{rng.choice('ABMPX')}{rng.randint(10**8, 10**9 - 1)}"
        proc = rng.choice(proc_pool)
        # leave roughly half the drafts un-approved so the "Awaiting your
        # approval" queue has a realistic backlog for a human to work
        auto_approve = rng.random() > 0.5
        rows.append(await drive_pa(org_id, payer, name, member_id, proc,
                                   emergency=False, auto_approve=auto_approve,
                                   when_days=rng.choice([3, 5, 7, 10, 14, 21])))

    demos: dict[str, str] = {}
    if with_demos:
        mer, cas = payers["Meridian Health Plan"], payers["Cascade Medicaid"]

        r = await drive_pa(org_id, mer, "Demo Always Auth",
                           member_for_response_band(rng, mer["id"], "72148", 88, "approved"),
                           "72148", emergency=False)
        demos["ALWAYS-AUTH"] = f'{r["status"]}'

        r = await drive_pa(org_id, mer, "Demo Routine", "M700000123", "99213", emergency=False)
        demos["ROUTINE"] = r["status"]

        r = await drive_pa(org_id, mer, "Demo No Code", "M700000124", "", emergency=False)
        demos["NO-CODE"] = r["status"]

        r = await drive_pa(org_id, mer, "Demo Emergency", "M700000125", "70553",
                           emergency=True, place="emergency")
        demos["EMERGENCY"] = r["status"]

        denied = await drive_pa(org_id, cas, "Demo Denied",
                                member_for_response_band(rng, cas["id"], "29881", 60, "denied"),
                                "29881", emergency=False)
        demos["DENIED"] = denied["status"]

        info = await drive_pa(org_id, cas, "Demo Info Needed",
                              member_for_response_band(rng, cas["id"], "62323", 60, "info_needed"),
                              "62323", emergency=False)
        demos["INFO-NEEDED"] = info["status"]
        if info["status"] == "info_needed":
            after = await resubmit(org_id, info["id"])
            demos["INFO-NEEDED-AFTER-RESUBMIT"] = after["status"]

    return {"org_id": org_id, "org_name": org_name, "email": email,
            "rows": rows, "demos": demos}


def report(res: dict) -> None:
    org_id, org_name = res["org_id"], res["org_name"]
    pas = svc_get("prior_authorizations",
                  {"organization_id": f"eq.{org_id}",
                   "select": "id,status,is_emergency,previous_auth_id"})
    superseded = {p["previous_auth_id"] for p in pas if p.get("previous_auth_id")}
    current = [p for p in pas if p["id"] not in superseded]
    total = len(current)

    dist: dict[str, int] = {}
    for p in current:
        dist[p["status"]] = dist.get(p["status"], 0) + 1

    print(f"\n  {org_name} — {total} prior authorizations "
          f"({sum(1 for p in current if p['is_emergency'])} emergency, "
          f"{len(pas) - total} superseded by a resubmit)")

    print("  DETERMINATION distribution (02.determine, deterministic):")
    det_order = ("required_draft", "not_required", "insufficient_info", "emergency_exempt")
    det_total = sum(dist.get(k, 0) for k in det_order) + dist.get("submission_declined", 0)
    for k in det_order:
        n = _det_count(current, k)
        if n:
            pct = 100 * n / total
            print(f"    {k:<20} {n:>3}  {pct:5.1f}%  {'#' * round(pct / 3)}")

    print("  PAYER RESPONSE distribution (02.submit -> simulate_response, deterministic):")
    resp_total = sum(1 for p in current if p["status"] in
                     ("auth_approved", "info_needed", "auth_denied", "submitted", "submitting"))
    for k, label in (("auth_approved", "approved"), ("info_needed", "info_needed"),
                     ("auth_denied", "denied")):
        n = dist.get(k, 0)
        if n:
            pct = 100 * n / resp_total if resp_total else 0
            print(f"    {label:<20} {n:>3}  {pct:5.1f}%  (of {resp_total} submitted)  {'#' * round(pct / 3)}")
    if dist.get("submission_declined"):
        print(f"    submission_declined  {dist['submission_declined']:>3}")

    esc = svc_get("escalations", {"organization_id": f"eq.{org_id}",
                                  "prior_authorization_id": "not.is.null", "select": "reason_code"})
    print(f"  prior-auth escalations (scheduled denied / info_needed): {len(esc)} "
          f"{[e['reason_code'] for e in esc]}")


def _det_count(current, status_key):
    """A row's *determination* result: required_draft rows may have progressed
    past that status, so infer from the terminal status too."""
    if status_key == "required_draft":
        return sum(1 for p in current if p["status"] in
                   ("required_draft", "submitting", "submitted", "auth_approved",
                    "info_needed", "auth_denied", "submission_declined"))
    return sum(1 for p in current if p["status"] == status_key)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=int, default=20)
    ap.add_argument("--b", type=int, default=14)
    args = ap.parse_args()

    print("Foresight — seeding prior authorizations")
    print(f"  Supabase : {API}")
    print("  02 prior-auth: deterministic simulation (no API key needed)\n")

    a = await seed_org(*SEED_ORGS[0], args.a, with_demos=True)
    b = await seed_org(*SEED_ORGS[1], args.b, with_demos=False)

    print("=" * 64)
    report(a)
    report(b)

    print("\n" + "=" * 64)
    print("Demo outcomes (clinic A):")
    for k, v in a["demos"].items():
        print(f"    {k:<28} {v}")

    d = a["demos"]
    ok = (
        d.get("ROUTINE") == "not_required"
        and d.get("NO-CODE") == "insufficient_info"
        and d.get("EMERGENCY") == "emergency_exempt"
        and d.get("ALWAYS-AUTH") in ("auth_approved", "info_needed", "auth_denied")
    )
    print()
    print("  " + ("OK " if ok else "!  ") +
          "emergency service -> emergency_exempt (no draft, no approval step, no escalation)")
    if d.get("INFO-NEEDED-AFTER-RESUBMIT"):
        print(f"  resubmit chain: info_needed -> {d['INFO-NEEDED-AFTER-RESUBMIT']}")
    if not ok:
        print("  ! one or more demo outcomes did not match expectations")

    print("\nLog in to review at /app/prior-auth:")
    for email, _ in SEED_ORGS:
        print(f"    {email}  /  {SEED_PW}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
