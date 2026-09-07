#!/usr/bin/env python3
"""Delete insurance-card images whose retention window has passed (Phase 4 / 03).

Confirmed scans keep their image until `image_retain_until`
(ocr.CONFIRM_IMAGE_RETENTION_DAYS after confirm); rejected scans keep it for
zero time. This job removes the Storage objects and stamps `image_purged_at`.
The extracted text on card_scans is untouched. Idempotent — safe to re-run and
safe to cron.

No ANTHROPIC_API_KEY needed. Needs the service-role key + the local stack (or
prod env vars). This is currently a MANUAL job — fine for the demo/pilot phase. Wiring it to a
scheduler (and setting the frequency) is gated on the compliance retention
review: docs/PHASE-4.md O-1, docs/BEFORE-PHI.md C-2.

Usage:
    python scripts/purge_expired_card_images.py            # do it
    python scripts/purge_expired_card_images.py --dry-run  # just report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _dotenv(path: pathlib.Path) -> dict:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _load_config() -> None:
    env = {**_dotenv(BACKEND / ".env"), **os.environ}
    url = env.get("SUPABASE_URL")
    svc = env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and svc):
        out = subprocess.run(
            ["npx", "--yes", "supabase", "status", "-o", "json"],
            capture_output=True, text=True, timeout=90, shell=(os.name == "nt"),
        ).stdout
        s = {k.upper(): v for k, v in json.loads(out).items()}
        url = url or s.get("API_URL")
        svc = svc or s.get("SERVICE_ROLE_KEY")
    if not svc:
        sys.exit("could not resolve the Supabase service_role key — is the local stack up?")
    os.environ.setdefault("SUPABASE_URL", (url or "http://127.0.0.1:54321").rstrip("/"))
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", svc)


_load_config()
sys.path.insert(0, str(BACKEND))
from app.card_retention import _expired_rows, purge_expired_images  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would be purged, delete nothing")
    args = ap.parse_args()

    from datetime import datetime, timezone  # noqa: PLC0415
    now_iso = datetime.now(timezone.utc).isoformat()

    if args.dry_run:
        rows = await _expired_rows(now_iso)
        print(f"[dry-run] {len(rows)} card image(s) past retention:")
        for r in rows:
            print(f"    {r['id']}  status={r['status']}  retain_until={r.get('image_retain_until')}")
        return 0

    result = await purge_expired_images()
    print(f"scanned {result['scanned']} past-retention scan(s); purged {result['purged']} image(s)")
    for e in result["errors"]:
        print(f"    ! {e['card_scan_id']}: {e['error']}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
