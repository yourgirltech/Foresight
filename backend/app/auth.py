"""Authentication + tenant-context resolution.

Flow for every protected request:
  1. Verify the Supabase access token (signature + expiry + audience).
     Supabase signs access tokens with an asymmetric key (ES256) exposed via
     JWKS; older / self-hosted setups may still use a shared HS256 secret. Both
     are handled.
  2. From the *verified* token take the user id — nothing tenant-related is
     ever read from the request body or query string.
  3. Load that user's profile through an RLS-scoped PostgREST call to learn
     their organization_id and role.

The resulting AuthContext is the only source of tenant identity in the app.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .supabase_rest import rest_get

_bearer = HTTPBearer(auto_error=True)

_ASYMMETRIC_ALGS = ("ES256", "RS256")


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    email: str | None
    organization_id: str | None
    role: str | None
    access_token: str

    @property
    def has_organization(self) -> bool:
        return self.organization_id is not None

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"


_jwk_clients: dict[str, jwt.PyJWKClient] = {}


def _jwk_client(settings: Settings) -> jwt.PyJWKClient:
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    client = _jwk_clients.get(url)
    if client is None:
        client = jwt.PyJWKClient(url, headers={"apikey": settings.supabase_anon_key}, lifespan=600)
        _jwk_clients[url] = client
    return client


def _decode_token(token: str, settings: Settings) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")
        opts = {"verify_aud": True}

        if alg in _ASYMMETRIC_ALGS:
            key = _jwk_client(settings).get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=list(_ASYMMETRIC_ALGS),
                              audience=settings.jwt_audience, options=opts)
        if alg == "HS256":
            return jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"],
                              audience=settings.jwt_audience, options=opts)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=f"unsupported token algorithm: {alg!r}")
    except HTTPException:
        raise
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid or expired token: {exc}",
        ) from exc


async def get_principal(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> dict:
    claims = _decode_token(credentials.credentials, settings)
    if not claims.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token has no subject")
    return {"claims": claims, "token": credentials.credentials}


async def get_auth_context(principal: dict = Depends(get_principal)) -> AuthContext:
    claims = principal["claims"]
    token = principal["token"]
    user_id = claims["sub"]

    # RLS-scoped: this returns the caller's own profile row and nothing else.
    rows = await rest_get(
        token,
        "/profiles",
        {"id": f"eq.{user_id}", "select": "id,organization_id,role,email"},
    )
    profile = rows[0] if rows else {}

    return AuthContext(
        user_id=user_id,
        email=claims.get("email") or profile.get("email"),
        organization_id=profile.get("organization_id"),
        role=profile.get("role"),
        access_token=token,
    )


async def require_organization(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not ctx.has_organization:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="user is authenticated but not yet attached to an organization",
        )
    return ctx


# --------------------------------------------------------------------------- #
# Phase 6 — the n8n automation principal.
#
# n8n calls /api/automation/voice-reminders/* with a static bearer token. This
# is NOT a Supabase role: it cannot reach PostgREST, the service-role key, or
# any table directly. It is accepted on no other router. Each automation
# endpoint resolves organization_id from the voice_reminders row it touches
# (docs/agents/17-voice-reminder-agent.md §2.3).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AutomationPrincipal:
    kind: str = "n8n-automation"


async def require_automation(
    request: Request, settings: Settings = Depends(get_settings)
) -> AutomationPrincipal:
    if not settings.n8n_service_token:
        # the automation surface is closed until the token is configured
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="automation endpoints are not configured (N8N_SERVICE_TOKEN unset)",
        )
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(
        token, settings.n8n_service_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid automation token",
        )
    return AutomationPrincipal()
