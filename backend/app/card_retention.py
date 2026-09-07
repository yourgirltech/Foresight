"""Card-image retention purge (Phase 4 / 03).

03 keeps the uploaded insurance-card image only briefly — `image_retain_until`
after a confirm (`ocr.CONFIRM_IMAGE_RETENTION_DAYS`), immediately after a reject.
This module deletes the Storage objects whose time is up and stamps
`image_purged_at` so the sweep is idempotent. The extracted text on card_scans
is never touched — only the picture goes.

Unlike `app.agents.db`, this is a **cross-tenant maintenance sweep**: it
deliberately scans every org's expired rows in one pass. It is a batch job, not
a request path — nothing here runs while serving a user. It is safe to run
repeatedly (idempotent) and safe to run by hand; wiring it to a scheduler — and
deciding how often — is a separate, still-open task gated on the compliance
retention review (docs/PHASE-4.md O-1, docs/BEFORE-PHI.md C-2).
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .config import get_settings
from .storage import delete_object

BUCKET = "card-scans"


def _client() -> httpx.AsyncClient:
    s = get_settings()
    if not s.supabase_service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set — the purge job cannot run")
    return httpx.AsyncClient(
        base_url=s.rest_url,
        headers={
            "apikey": s.supabase_service_role_key,
            "Authorization": f"Bearer {s.supabase_service_role_key}",
            "Accept": "application/json",
        },
        timeout=30.0,
    )


async def _expired_rows(now_iso: str) -> list[dict]:
    """Rows whose image should already be gone and has not been purged yet:
      * a confirmed scan past its image_retain_until, OR
      * a rejected scan (retention 0 — the inline delete on reject may have
        failed, so sweep it defensively).
    """
    params = {
        "select": "id,organization_id,image_path,status,image_retain_until",
        "image_purged_at": "is.null",
        "or": f"(and(status.eq.confirmed,image_retain_until.lt.{now_iso}),status.eq.rejected)",
    }
    async with _client() as c:
        r = await c.get("card_scans", params=params)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else [data]


async def _mark_purged(scan_id: str, org_id: str, now_iso: str) -> None:
    async with _client() as c:
        r = await c.patch(
            "card_scans",
            params={"id": f"eq.{scan_id}", "organization_id": f"eq.{org_id}"},
            json={"image_purged_at": now_iso},
            headers={"Prefer": "return=minimal", "Content-Type": "application/json"},
        )
        r.raise_for_status()


async def purge_expired_images(*, now: datetime | None = None) -> dict:
    """Delete every past-retention card image and stamp image_purged_at.
    Returns {"scanned", "purged", "errors": [...]}. Idempotent."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()

    rows = await _expired_rows(now_iso)
    purged = 0
    errors: list[dict] = []
    for row in rows:
        try:
            await delete_object(BUCKET, row["image_path"])
            await _mark_purged(row["id"], row["organization_id"], now_iso)
            purged += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
            errors.append({"card_scan_id": row["id"], "error": str(exc)})

    return {"scanned": len(rows), "purged": purged, "errors": errors}
