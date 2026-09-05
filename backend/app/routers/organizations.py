from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import AuthContext, require_organization
from ..supabase_rest import rest_get

router = APIRouter(tags=["organizations"])


@router.get("/api/organizations")
async def list_organizations(
    ctx: AuthContext = Depends(require_organization),
    organization_id: str | None = Query(
        default=None,
        description=(
            "Accepted but deliberately IGNORED. Tenant scope comes from the "
            "verified session, never from the client. Present so the isolation "
            "test can prove a spoofed value changes nothing."
        ),
    ),
) -> dict:
    if organization_id and organization_id != ctx.organization_id:
        # A client asking for someone else's tenant. We do not honor it; we
        # do not 403 either — we simply answer with the caller's own scope.
        pass

    rows = await rest_get(
        ctx.access_token,
        "/organizations",
        {"select": "id,name,created_at"},
    )
    return {
        "requested_organization_id": organization_id,
        "effective_organization_id": ctx.organization_id,
        "organizations": rows,
    }


@router.get("/api/organizations/{org_id}")
async def get_organization(
    org_id: str,
    ctx: AuthContext = Depends(require_organization),
) -> dict:
    """Fetch one organization by id, through the caller's RLS scope.

    Ask for Clinic B's id while signed in as Clinic A and Postgres returns zero
    rows — so this 404s. The id in the URL never widens what you can see.
    """
    rows = await rest_get(
        ctx.access_token,
        "/organizations",
        {"id": f"eq.{org_id}", "select": "id,name,created_at"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    return rows[0]


@router.get("/api/team")
async def list_team(ctx: AuthContext = Depends(require_organization)) -> dict:
    """Profiles visible to the caller — RLS limits this to their own clinic."""
    rows = await rest_get(
        ctx.access_token,
        "/profiles",
        {"select": "id,email,full_name,role,organization_id", "order": "created_at"},
    )
    return {"organization_id": ctx.organization_id, "members": rows}
