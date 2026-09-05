from fastapi import APIRouter, Depends

from ..auth import AuthContext, get_auth_context

router = APIRouter(tags=["me"])


@router.get("/api/me")
async def read_me(ctx: AuthContext = Depends(get_auth_context)) -> dict:
    """Who the server thinks you are — derived entirely from the verified JWT.

    The frontend uses `organization_id` here to decide whether to send the user
    to onboarding or into the app shell.
    """
    return {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "organization_id": ctx.organization_id,
        "role": ctx.role,
        "has_organization": ctx.has_organization,
    }
