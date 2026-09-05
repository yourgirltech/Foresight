#!/usr/bin/env python3
"""Foresight — two-clinic data-isolation proof.

Creates two independent clinics through the real signup / onboarding / invite
paths, then, signed in as Clinic A, tries every way we can think of to read or
mutate Clinic B's data — directly against PostgREST and through the FastAPI
backend — and asserts every attempt returns nothing or is rejected.

Zero third-party dependencies (stdlib only) so it runs anywhere Python 3.11+ is.

Usage:
    python tests/isolation_test.py                 # needs `supabase start` + backend on :8000
    python tests/isolation_test.py --skip-backend  # PostgREST checks only

Config resolution order: CLI env vars -> `supabase status -o json` -> local defaults.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

# --------------------------------------------------------------------------- #
# tiny HTTP helper
# --------------------------------------------------------------------------- #

TIMEOUT = 15


def http(method: str, url: str, headers: dict | None = None, body: dict | list | None = None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        status = exc.code
    except urllib.error.URLError as exc:
        return 0, {"_urlerror": str(exc)}
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def load_config() -> dict:
    cfg = {
        "api_url": os.environ.get("SUPABASE_URL"),
        "anon_key": os.environ.get("SUPABASE_ANON_KEY"),
        "service_role_key": os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        "backend_url": os.environ.get("API_BASE_URL", "http://localhost:8000"),
    }
    if not (cfg["api_url"] and cfg["anon_key"] and cfg["service_role_key"]):
        try:
            out = subprocess.run(
                ["npx", "--yes", "supabase", "status", "-o", "json"],
                capture_output=True, text=True, timeout=60, shell=(os.name == "nt"),
            ).stdout
            status = json.loads(out)
            norm = {k.upper(): v for k, v in status.items()}
            cfg["api_url"] = cfg["api_url"] or norm.get("API_URL")
            cfg["anon_key"] = cfg["anon_key"] or norm.get("ANON_KEY")
            cfg["service_role_key"] = cfg["service_role_key"] or norm.get("SERVICE_ROLE_KEY")
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"could not resolve Supabase config from env or `supabase status`: {exc}")

    cfg["api_url"] = (cfg["api_url"] or "http://127.0.0.1:54321").rstrip("/")
    if not cfg["anon_key"] or not cfg["service_role_key"]:
        sys.exit("missing anon key / service_role key — is the local stack running?")
    return cfg


# --------------------------------------------------------------------------- #
# Supabase helpers
# --------------------------------------------------------------------------- #


class Supa:
    def __init__(self, cfg: dict):
        self.api = cfg["api_url"]
        self.anon = cfg["anon_key"]
        self.service = cfg["service_role_key"]
        self.rest = f"{self.api}/rest/v1"
        self.auth = f"{self.api}/auth/v1"

    # --- auth admin (service role) ---
    def admin_headers(self) -> dict:
        return {"apikey": self.service, "Authorization": f"Bearer {self.service}"}

    def list_users(self) -> list[dict]:
        _, body = http("GET", f"{self.auth}/admin/users?per_page=200", self.admin_headers())
        return (body or {}).get("users", []) if isinstance(body, dict) else []

    def delete_user(self, user_id: str) -> None:
        http("DELETE", f"{self.auth}/admin/users/{user_id}", self.admin_headers())

    def create_user(self, email: str, password: str) -> str:
        status, body = http(
            "POST", f"{self.auth}/admin/users", self.admin_headers(),
            {"email": email, "password": password, "email_confirm": True},
        )
        if status not in (200, 201) or not isinstance(body, dict) or "id" not in body:
            sys.exit(f"failed to create user {email}: {status} {body}")
        return body["id"]

    def sign_in(self, email: str, password: str) -> str:
        status, body = http(
            "POST", f"{self.auth}/token?grant_type=password",
            {"apikey": self.anon, "Content-Type": "application/json"},
            {"email": email, "password": password},
        )
        if status != 200 or not isinstance(body, dict) or "access_token" not in body:
            sys.exit(f"failed to sign in {email}: {status} {body}")
        return body["access_token"]

    # --- data-plane as a given identity ---
    def user_headers(self, token: str) -> dict:
        return {"apikey": self.anon, "Authorization": f"Bearer {token}"}

    def rpc(self, token: str, fn: str, args: dict):
        return http("POST", f"{self.rest}/rpc/{fn}", self.user_headers(token), args)

    def select(self, token: str | None, table: str, query: str = "select=*"):
        headers = self.user_headers(token) if token else {"apikey": self.anon}
        return http("GET", f"{self.rest}/{table}?{query}", headers)

    def patch(self, token: str, table: str, query: str, body: dict):
        headers = {**self.user_headers(token), "Prefer": "return=representation"}
        return http("PATCH", f"{self.rest}/{table}?{query}", headers, body)

    def insert(self, token: str, table: str, body: dict):
        headers = {**self.user_headers(token), "Prefer": "return=representation"}
        return http("POST", f"{self.rest}/{table}", headers, body)

    # --- service-role reads (ground truth, bypasses RLS) ---
    def truth_select(self, table: str, query: str = "select=*"):
        return http("GET", f"{self.rest}/{table}?{query}", self.admin_headers())

    def promote_platform_admin(self, user_id: str):
        return http(
            "PATCH", f"{self.rest}/profiles?id=eq.{user_id}",
            {**self.admin_headers(), "Prefer": "return=representation"},
            {"role": "platform_admin"},
        )


# --------------------------------------------------------------------------- #
# assertions
# --------------------------------------------------------------------------- #

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not ok:
        line += f"\n         -> {detail}"
    print(line)


def rows(body) -> list:
    return body if isinstance(body, list) else []


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

TEST_EMAILS = {
    "a_admin": "a_admin@foresight.test",
    "a_staff": "a_staff@foresight.test",
    "b_admin": "b_admin@foresight.test",
    "platform": "platform@foresight.test",
}
PW = "Foresight-test-123!"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-backend", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    supa = Supa(cfg)
    backend = cfg["backend_url"].rstrip("/")

    print(f"Supabase API : {supa.api}")
    print(f"Backend      : {'(skipped)' if args.skip_backend else backend}")
    print()

    # ---- clean slate ----
    existing = {u["email"]: u["id"] for u in supa.list_users()}
    for email in TEST_EMAILS.values():
        if email in existing:
            supa.delete_user(existing[email])

    # ---- create identities ----
    uid = {name: supa.create_user(email, PW) for name, email in TEST_EMAILS.items()}
    tok = {name: supa.sign_in(email, PW) for name, email in TEST_EMAILS.items()}

    print("== Onboarding: two independent clinics ==")

    st, org_a = supa.rpc(tok["a_admin"], "bootstrap_organization", {"org_name": "Clinic A — Riverside"})
    check("Clinic A created via bootstrap_organization", st == 200 and isinstance(org_a, dict) and org_a.get("name") == "Clinic A — Riverside", f"{st} {org_a}")
    st, org_b = supa.rpc(tok["b_admin"], "bootstrap_organization", {"org_name": "Clinic B — Lakeside"})
    check("Clinic B created via bootstrap_organization", st == 200 and isinstance(org_b, dict), f"{st} {org_b}")
    org_a_id, org_b_id = org_a["id"], org_b["id"]
    check("the two clinics have different ids", org_a_id != org_b_id)

    # a_admin is clinic_admin of A
    st, prof = supa.select(tok["a_admin"], "profiles", f"id=eq.{uid['a_admin']}&select=role,organization_id")
    check("clinic creator is clinic_admin of their org",
          rows(prof) and rows(prof)[0] == {"role": "clinic_admin", "organization_id": org_a_id}, f"{st} {prof}")

    print("\n== Invite flow: teammate joins Clinic A as staff ==")
    st, inv = supa.rpc(tok["a_admin"], "create_invitation", {"invitee_email": TEST_EMAILS["a_staff"], "invitee_role": "staff"})
    check("clinic_admin can create an invitation", st == 200 and isinstance(inv, dict) and "token" in inv, f"{st} {inv}")
    st, joined = supa.rpc(tok["a_staff"], "accept_invitation", {"invitation_token": inv["token"]})
    check("invitee can accept and joins the inviting org", st == 200 and isinstance(joined, dict) and joined.get("id") == org_a_id, f"{st} {joined}")
    st, sp = supa.select(tok["a_staff"], "profiles", f"id=eq.{uid['a_staff']}&select=role,organization_id")
    check("invitee is scoped to Clinic A as staff",
          rows(sp) and rows(sp)[0] == {"role": "staff", "organization_id": org_a_id}, f"{st} {sp}")

    # platform admin promoted out of band (service role)
    st, _ = supa.promote_platform_admin(uid["platform"])
    check("platform_admin promoted via service role", st == 200, str(st))
    tok["platform"] = supa.sign_in(TEST_EMAILS["platform"], PW)  # refresh claims

    print("\n== Isolation: signed in as Clinic A, try to reach Clinic B ==")
    ta = tok["a_admin"]

    st, b = supa.select(ta, "organizations")
    check("A lists organizations -> sees only its own", rows(b) and {r["id"] for r in rows(b)} == {org_a_id}, f"{st} {b}")

    st, b = supa.select(ta, "organizations", f"id=eq.{org_b_id}")
    check("A filters organizations by B's id -> empty", rows(b) == [], f"{st} {b}")

    st, b = supa.select(ta, "profiles")
    ids = {r["id"] for r in rows(b)}
    check("A lists profiles -> only Clinic A members, no Clinic B staff",
          ids == {uid["a_admin"], uid["a_staff"]} and uid["b_admin"] not in ids, f"{st} {b}")

    st, b = supa.select(ta, "profiles", f"organization_id=eq.{org_b_id}")
    check("A filters profiles by B's org id -> empty", rows(b) == [], f"{st} {b}")

    st, b = supa.select(ta, "invitations")
    check("A lists invitations -> all scoped to Clinic A",
          all(r["organization_id"] == org_a_id for r in rows(b)), f"{st} {b}")

    st, b = supa.patch(ta, "organizations", f"id=eq.{org_b_id}", {"name": "PWNED"})
    _, truth = supa.truth_select("organizations", f"id=eq.{org_b_id}&select=name")
    check("A tries to rename Clinic B -> no row affected, name unchanged",
          rows(b) == [] and rows(truth) and rows(truth)[0]["name"] == "Clinic B — Lakeside", f"patch={st}{b} truth={truth}")

    st, b = supa.insert(ta, "organizations", {"name": "rogueclinic", "id": str(uuid.uuid4())})
    _, truth = supa.truth_select("organizations", "name=eq.rogueclinic&select=id")
    check("A tries to INSERT a new organization directly -> rejected, nothing created",
          st >= 400 and rows(truth) == [], f"{st} {b}")

    st, b = supa.rpc(ta, "accept_invitation", {"invitation_token": str(uuid.uuid4())})
    check("A (already in an org) tries accept_invitation -> rejected", st >= 400, f"{st} {b}")

    print("\n== Isolation via the FastAPI backend ==")
    if args.skip_backend:
        check("backend checks", True, "skipped (--skip-backend)")
    else:
        h = {"Authorization": f"Bearer {ta}"}
        st, b = http("GET", f"{backend}/api/organizations?organization_id={org_b_id}", h)
        ok = (
            st == 200 and isinstance(b, dict)
            and b.get("effective_organization_id") == org_a_id
            and [r["id"] for r in b.get("organizations", [])] == [org_a_id]
        )
        check("backend ignores spoofed ?organization_id and returns only Clinic A", ok, f"{st} {b}")

        st, b = http("GET", f"{backend}/api/organizations/{org_b_id}", h)
        check("backend GET /api/organizations/{B id} -> 404", st == 404, f"{st} {b}")

        st, b = http("GET", f"{backend}/api/team", h)
        members = {m["id"] for m in b.get("members", [])} if isinstance(b, dict) else set()
        check("backend GET /api/team -> only Clinic A members", members == {uid["a_admin"], uid["a_staff"]}, f"{st} {b}")

    print("\n== Privilege escalation is blocked (signed in as Clinic A staff) ==")
    ts = tok["a_staff"]

    st, b = supa.patch(ts, "profiles", f"id=eq.{uid['a_staff']}", {"role": "clinic_admin"})
    _, truth = supa.truth_select("profiles", f"id=eq.{uid['a_staff']}&select=role")
    check("staff tries to make itself clinic_admin -> blocked, role unchanged",
          rows(truth) and rows(truth)[0]["role"] == "staff", f"patch={st}{b} truth={truth}")

    st, b = supa.patch(ts, "profiles", f"id=eq.{uid['a_staff']}", {"organization_id": org_b_id})
    _, truth = supa.truth_select("profiles", f"id=eq.{uid['a_staff']}&select=organization_id")
    check("staff tries to move itself into Clinic B -> blocked, org unchanged",
          rows(truth) and rows(truth)[0]["organization_id"] == org_a_id, f"patch={st}{b} truth={truth}")

    st, b = supa.rpc(ts, "create_invitation", {"invitee_email": "x@y.test", "invitee_role": "staff"})
    check("staff tries to invite a teammate -> rejected", st >= 400, f"{st} {b}")

    st, b = supa.patch(ts, "profiles", f"id=eq.{uid['b_admin']}", {"full_name": "tampered"})
    check("staff tries to edit a Clinic B profile -> no row affected", rows(b) == [], f"{st} {b}")

    print("\n== Symmetric check: signed in as Clinic B ==")
    tb = tok["b_admin"]
    st, b = supa.select(tb, "profiles")
    check("B lists profiles -> only itself", {r["id"] for r in rows(b)} == {uid["b_admin"]}, f"{st} {b}")
    st, b = supa.select(tb, "organizations")
    check("B lists organizations -> only Clinic B", {r["id"] for r in rows(b)} == {org_b_id}, f"{st} {b}")

    print("\n== platform_admin sees across tenants (by design) ==")
    tp = tok["platform"]
    st, b = supa.select(tp, "organizations")
    check("platform_admin sees both clinics", {org_a_id, org_b_id} <= {r["id"] for r in rows(b)}, f"{st} {b}")
    st, b = supa.select(tp, "profiles")
    check("platform_admin sees profiles from both clinics",
          {uid["a_admin"], uid["a_staff"], uid["b_admin"]} <= {r["id"] for r in rows(b)}, f"{st} {b}")

    print("\n== Unauthenticated (anon key only) ==")
    st, b = supa.select(None, "organizations")
    check("anon sees no organizations", rows(b) == [] or st >= 400, f"{st} {b}")
    st, b = supa.select(None, "profiles")
    check("anon sees no profiles", rows(b) == [] or st >= 400, f"{st} {b}")

    # ---- summary ----
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 64)
    if passed == total:
        print(f"ALL {total} CHECKS PASSED — cross-tenant isolation holds.")
        return 0
    print(f"{passed}/{total} passed — {total - passed} FAILED:")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  - {name}: {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
