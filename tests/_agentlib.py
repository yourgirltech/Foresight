"""Shared helpers for the agent integration tests (e2e + agent isolation).

Resolves local Supabase config, wires it into the backend settings, and gives a
tiny service-role PostgREST client plus a real signup/bootstrap `ensure_org`.
Stdlib only.
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
SEED_PW = "Foresight-agenttest-123!"


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
                out[k.strip()] = v.strip()
    return out


def _resolve_config() -> dict:
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


CFG = _resolve_config()
os.environ.setdefault("SUPABASE_URL", CFG["api_url"])
os.environ.setdefault("SUPABASE_ANON_KEY", CFG["anon_key"] or "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", CFG["service_role_key"])
os.environ.setdefault("SUPABASE_JWT_SECRET", CFG["jwt_secret"] or "")
sys.path.insert(0, str(BACKEND))

API = CFG["api_url"]
REST = f"{API}/rest/v1"
AUTH = f"{API}/auth/v1"
SVC = CFG["service_role_key"]
ANON = CFG["anon_key"]


def _h():
    return {"apikey": SVC, "Authorization": f"Bearer {SVC}"}


def svc_get(path, params):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return _http("GET", f"{REST}/{path}?{q}", _h())[1] or []


def svc_write(method, path, params, body):
    q = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"{REST}/{path}" + (f"?{q}" if q else "")
    st, resp = _http(method, url, {**_h(), "Prefer": "return=representation"}, body)
    if st >= 300:
        raise RuntimeError(f"{method} {path} -> {st} {resp}")
    return resp or []


def ensure_org(email: str, org_name: str) -> tuple[str, str]:
    """(organization_id, admin_user_id) via the real signup + bootstrap path.
    Reuses an existing org of the same name; wipes its claims for a clean run."""
    users = _http("GET", f"{AUTH}/admin/users?per_page=200", _h())[1] or {}
    by_email = {u["email"]: u["id"] for u in users.get("users", [])}
    uid = by_email.get(email)
    if uid is None:
        st, body = _http("POST", f"{AUTH}/admin/users", _h(),
                         {"email": email, "password": SEED_PW, "email_confirm": True})
        if st not in (200, 201):
            sys.exit(f"create user {email}: {st} {body}")
        uid = body["id"]

    existing = svc_get("organizations", {"name": f"eq.{org_name}", "select": "id"})
    if existing:
        org_id = existing[0]["id"]
        svc_write("PATCH", "profiles", {"id": f"eq.{uid}"},
                  {"organization_id": org_id, "role": "clinic_admin"})
    else:
        st, body = _http("POST", f"{AUTH}/token?grant_type=password",
                         {"apikey": ANON}, {"email": email, "password": SEED_PW})
        token = body["access_token"]
        st, body = _http("POST", f"{REST}/rpc/bootstrap_organization",
                         {"apikey": ANON, "Authorization": f"Bearer {token}"},
                         {"org_name": org_name})
        if st != 200:
            sys.exit(f"bootstrap_organization {org_name}: {st} {body}")
        org_id = body["id"]

    # order matters: children before parents (payers is on-delete-restrict from
    # both claims and eligibility_checks).
    for tbl in ("eligibility_checks", "appointments", "claims", "payers",
                "activity_log", "escalations"):
        svc_write("DELETE", tbl, {"organization_id": f"eq.{org_id}"}, None)
    return org_id, uid


def user_token(email: str) -> str:
    st, body = _http("POST", f"{AUTH}/token?grant_type=password",
                     {"apikey": ANON}, {"email": email, "password": SEED_PW})
    if st != 200:
        sys.exit(f"sign in {email}: {st} {body}")
    return body["access_token"]


class Check:
    def __init__(self) -> None:
        self.passed = self.failed = 0

    def __call__(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        line = f"  [{mark}] {name}"
        if detail and not ok:
            line += f"\n         -> {detail}"
        print(line)

    def summary(self, title: str) -> int:
        total = self.passed + self.failed
        print("\n" + "=" * 64)
        if self.failed == 0:
            print(f"ALL {total} CHECKS PASSED — {title}")
            return 0
        print(f"{self.passed}/{total} passed — {self.failed} FAILED")
        return 1
