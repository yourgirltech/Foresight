#!/usr/bin/env python3
"""Seed a flat self-pay price list for the cost-estimate module (Phase 4 / 05).

A documented, illustrative price list for the common codes already used
elsewhere (the prior-auth 70553 / 72148 / 29881 / … set plus routine office
codes), so a Good Faith Estimate has something to price against. Flat pricing
only — one base_price per code; insurance-adjusted pricing is a future
refinement, not Phase 4.

Idempotent (upsert on organization_id + procedure_code). No ANTHROPIC_API_KEY
needed. Usage:  python scripts/seed_procedure_prices.py
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
    cfg = {"api_url": env.get("SUPABASE_URL"), "anon_key": env.get("SUPABASE_ANON_KEY"),
           "service_role_key": env.get("SUPABASE_SERVICE_ROLE_KEY"),
           "jwt_secret": env.get("SUPABASE_JWT_SECRET")}
    if not all(cfg.values()):
        out = subprocess.run(["npx", "--yes", "supabase", "status", "-o", "json"],
                             capture_output=True, text=True, timeout=90,
                             shell=(os.name == "nt")).stdout
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
API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]
SEED_PW = "Foresight-seed-123!"


def _admin():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def svc_get(path, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return _http("GET", f"{REST}/{path}?{q}", _admin())[1] or []


def svc_write(method, path, params, body, extra=None):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**_admin(), "Prefer": "return=representation", **(extra or {})}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org(email: str, org_name: str) -> str:
    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    if existing:
        return existing[0]["id"]
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _admin())[1] or {}
    if email not in {u["email"] for u in users.get("users", [])}:
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


# code -> (description, flat self-pay base price USD). Illustrative, documented,
# NOT a real chargemaster — the point is a reproducible demo, like 06's weights.
PRICES: dict[str, tuple[str, float]] = {
    "99212": ("Office visit, established patient (straightforward)", 95.00),
    "99213": ("Office visit, established patient (low complexity)", 145.00),
    "99214": ("Office visit, established patient (moderate complexity)", 210.00),
    "99203": ("Office visit, new patient (low complexity)", 185.00),
    "99204": ("Office visit, new patient (moderate complexity)", 290.00),
    "36415": ("Routine venipuncture (blood draw)", 15.25),
    "80053": ("Comprehensive metabolic panel", 49.99),
    "85025": ("Complete blood count with differential", 38.50),
    "81003": ("Urinalysis, automated, without microscopy", 12.00),
    "93000": ("Electrocardiogram, complete", 62.00),
    "71046": ("Chest X-ray, 2 views", 135.00),
    "72148": ("MRI, lumbar spine, without contrast", 1200.50),
    "72141": ("MRI, cervical spine, without contrast", 1180.00),
    "70553": ("MRI, brain, with and without contrast", 1650.00),
    "29881": ("Knee arthroscopy with meniscectomy", 4200.00),
    "29827": ("Shoulder arthroscopy with rotator cuff repair", 6100.00),
    "62323": ("Lumbar transforaminal epidural steroid injection", 850.00),
    "20610": ("Aspiration/injection of a major joint", 175.00),
    "17000": ("Destruction of a premalignant skin lesion (first)", 130.00),
    "10060": ("Incision and drainage of an abscess (simple)", 220.00),
    "90471": ("Immunization administration (first)", 30.00),
    "96372": ("Therapeutic injection, subcutaneous or intramuscular", 40.00),
}

SEED_ORGS = [
    ("seed_admin_a@foresight.test", "Foresight Seed — Riverside Family Medicine"),
    ("seed_admin_b@foresight.test", "Foresight Seed — Lakeside Pediatrics"),
]


def seed_org(email: str, org_name: str) -> int:
    org_id = ensure_org(email, org_name)
    have = {p["procedure_code"]: p for p in
            svc_get("procedure_prices", {"organization_id": f"eq.{org_id}", "select": "*"})}
    written = 0
    for code, (desc, price_usd) in PRICES.items():
        fields = {"description": desc, "base_price": price_usd, "active": True}
        if code in have:
            svc_write("PATCH", "procedure_prices",
                      {"organization_id": f"eq.{org_id}", "id": f"eq.{have[code]['id']}"}, fields)
        else:
            svc_write("POST", "procedure_prices", {},
                      {"organization_id": org_id, "procedure_code": code, **fields})
        written += 1
    return written


def main() -> int:
    print("Foresight — seeding self-pay procedure prices (Phase 4 / 05)")
    print(f"  Supabase : {API}\n")
    for email, name in SEED_ORGS:
        n = seed_org(email, name)
        print(f"  {name}: {n} procedure prices")
    print(f"\n  {len(PRICES)} codes, flat self-pay pricing (illustrative — not a real chargemaster).")
    print("  Generate an estimate from a self-pay appointment's detail page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
