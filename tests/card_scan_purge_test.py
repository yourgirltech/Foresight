#!/usr/bin/env python3
"""03 — card-image retention purge.

Proves scripts/purge_expired_card_images.py / app.card_retention:
  * a CONFIRMED scan past image_retain_until -> object deleted, image_purged_at set;
  * a CONFIRMED scan still within retention -> untouched;
  * a REJECTED scan whose inline delete failed -> swept defensively (retention 0);
  * the extracted text on every row is preserved;
  * the sweep is idempotent (a second run purges nothing, no errors);
  * it sweeps every tenant in one pass but each row stays org-scoped.

Run (local stack up):  python tests/card_scan_purge_test.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

from _agentlib import API, Check, ensure_org, _h, svc_get, svc_write  # noqa: E402

from app.card_retention import purge_expired_images  # noqa: E402
from app.storage import upload_object  # noqa: E402

BUCKET = "card-scans"
NOW = datetime.now(timezone.utc)


def _object_exists(path: str) -> bool:
    q = urllib.parse.quote(path)
    req = urllib.request.Request(f"{API}/storage/v1/object/{BUCKET}/{q}", headers=_h())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False


async def _seed_scan(org_id: str, status: str, retain_until: datetime | None) -> dict:
    scan_id = str(uuid.uuid4())
    path = f"{org_id}/{scan_id}.png"
    await upload_object(BUCKET, path, b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")
    return svc_write("POST", "card_scans", {}, {
        "id": scan_id, "organization_id": org_id, "patient_name": "Purge Probe",
        "image_path": path, "image_mime": "image/png", "status": status,
        "extracted_fields": {"member_id": "KEEP-ME-123"},
        "image_retain_until": retain_until.isoformat() if retain_until else None,
    })[0]


async def main() -> int:
    chk = Check()
    print("03 — card-image retention purge\n")

    org_a, _ = ensure_org("purge_a@foresight.test", "Purge — Clinic A")
    org_b, _ = ensure_org("purge_b@foresight.test", "Purge — Clinic B")
    # ensure_org does not touch card_scans — clear ours so scanned counts are exact
    for org in (org_a, org_b):
        svc_write("DELETE", "card_scans", {"organization_id": f"eq.{org}"}, None)

    expired_a = await _seed_scan(org_a, "confirmed", NOW - timedelta(days=1))
    fresh_a = await _seed_scan(org_a, "confirmed", NOW + timedelta(days=30))
    rejected_a = await _seed_scan(org_a, "rejected", None)
    expired_b = await _seed_scan(org_b, "confirmed", NOW - timedelta(hours=2))

    chk("all four seed images exist in Storage before the purge",
        all(_object_exists(s["image_path"]) for s in (expired_a, fresh_a, rejected_a, expired_b)))

    result = await purge_expired_images(now=NOW)
    chk("purge covered our 3 past-retention rows (2x expired confirmed + 1 rejected), 0 errors",
        result["scanned"] >= 3 and result["purged"] == result["scanned"]
        and result["errors"] == [], str(result))

    chk("expired confirmed image (A) is gone from Storage",
        not _object_exists(expired_a["image_path"]))
    chk("rejected image (A) is gone from Storage",
        not _object_exists(rejected_a["image_path"]))
    chk("expired image in tenant B is gone (one sweep covers every org)",
        not _object_exists(expired_b["image_path"]))
    chk("the still-in-retention confirmed image (A) is UNTOUCHED",
        _object_exists(fresh_a["image_path"]))

    rows = {r["id"]: r for r in svc_get("card_scans", {
        "select": "id,organization_id,image_purged_at,extracted_fields,status",
        "id": f"in.({expired_a['id']},{fresh_a['id']},{rejected_a['id']},{expired_b['id']})"})}
    chk("expired A row: image_purged_at stamped, still org A, text preserved",
        rows[expired_a["id"]]["image_purged_at"] is not None
        and rows[expired_a["id"]]["organization_id"] == org_a
        and rows[expired_a["id"]]["extracted_fields"].get("member_id") == "KEEP-ME-123",
        str(rows[expired_a["id"]]))
    chk("expired B row: image_purged_at stamped, still org B",
        rows[expired_b["id"]]["image_purged_at"] is not None
        and rows[expired_b["id"]]["organization_id"] == org_b, str(rows[expired_b["id"]]))
    chk("fresh A row: image_purged_at still null",
        rows[fresh_a["id"]]["image_purged_at"] is None, str(rows[fresh_a["id"]]))

    again = await purge_expired_images(now=NOW)
    chk("second run is idempotent — nothing left to purge, no errors",
        again["scanned"] == 0 and again["purged"] == 0 and again["errors"] == [], str(again))

    return chk.summary("card images are deleted past retention; the text is kept.")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
