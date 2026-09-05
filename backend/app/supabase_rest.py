"""Thin PostgREST client.

The single rule enforced here: when serving a user request we talk to PostgREST
with *the user's own access token*. Postgres then applies the same Row Level
Security policies the frontend is subject to. The backend never swaps in the
service-role key to "help" a query along.
"""
from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings


def user_client(access_token: str) -> httpx.AsyncClient:
    """An httpx client bound to one end-user's identity.

    `apikey` is the anon key (PostgREST requires it to route the request);
    `Authorization` carries the user's JWT, which is what RLS keys off.
    """
    settings = get_settings()
    return httpx.AsyncClient(
        base_url=settings.rest_url,
        headers={
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        timeout=10.0,
    )


async def rest_get(access_token: str, path: str, params: dict[str, Any] | None = None) -> list[dict]:
    # `path` is relative to <supabase>/rest/v1/ — pass e.g. "profiles", no leading slash.
    async with user_client(access_token) as client:
        resp = await client.get(path.lstrip("/"), params=params or {})
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]


async def rest_patch(
    access_token: str,
    path: str,
    params: dict[str, Any],
    body: dict[str, Any],
) -> list[dict]:
    """PATCH through the user's own RLS scope. `params` selects the target rows
    (e.g. {"id": "eq.<uuid>"}); Postgres RLS still applies on top."""
    async with user_client(access_token) as client:
        resp = await client.patch(
            path.lstrip("/"),
            params=params,
            json=body,
            headers={"Prefer": "return=representation"},
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return []
        data = resp.json()
        return data if isinstance(data, list) else [data]
