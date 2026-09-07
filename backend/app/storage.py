"""Service-role Supabase Storage client (Phase 4 / 03-ocr-agent).

The `card-scans` bucket is PRIVATE and has no policies on storage.objects, so
anon + authenticated are denied outright. Only this module touches it, and only
with the service-role key — the browser never uploads or downloads a card image
directly (Phase 2-3 discipline). The backend mints a short-lived signed URL when
a reviewer needs to see the picture.

Object path convention: `<organization_id>/<card_scan_id>.<ext>`. The caller
resolves `organization_id` from the verified session before writing — never from
a request body.
"""
from __future__ import annotations

import httpx

from .config import get_settings

# Reviewer looks at the image for a minute or two — a 10-minute URL is plenty and
# limits how long a leaked link is useful.
SIGNED_URL_TTL_SECONDS = 600


def _storage_base() -> str:
    return f"{get_settings().supabase_url.rstrip('/')}/storage/v1"


def _headers() -> dict[str, str]:
    key = get_settings().supabase_service_role_key
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set — Storage is unavailable")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


async def upload_object(bucket: str, path: str, data: bytes, content_type: str) -> None:
    """Create-or-replace the object at `path` in `bucket`."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_storage_base()}/object/{bucket}/{path}",
            headers={**_headers(), "Content-Type": content_type, "x-upsert": "true"},
            content=data,
        )
        resp.raise_for_status()


async def create_signed_url(
    bucket: str, path: str, *, expires_in: int = SIGNED_URL_TTL_SECONDS
) -> str:
    """Return an absolute, time-limited URL a browser can GET the object from."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_storage_base()}/object/sign/{bucket}/{path}",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"expiresIn": expires_in},
        )
        resp.raise_for_status()
        signed = resp.json().get("signedURL") or resp.json().get("signedUrl")
    if not signed:
        raise RuntimeError(f"Storage did not return a signed URL for {bucket}/{path}")
    return f"{_storage_base()}{signed}" if signed.startswith("/") else signed


async def delete_object(bucket: str, path: str) -> None:
    """Remove the object. Missing object is not an error (idempotent cleanup)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(
            "DELETE",
            f"{_storage_base()}/object/{bucket}/{path}",
            headers=_headers(),
        )
        if resp.status_code not in (200, 404):
            resp.raise_for_status()
