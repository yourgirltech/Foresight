from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import (
    appointments,
    card_scans,
    claims,
    coverages,
    dashboard,
    demo,
    me,
    organizations,
    prior_auth,
)

settings = get_settings()

app = FastAPI(
    title="Foresight API",
    version="0.1.0",
    summary="Phase 1 foundation: session verification + server-side tenant scoping.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(me.router)
app.include_router(organizations.router)
app.include_router(claims.router)
app.include_router(appointments.router)
app.include_router(prior_auth.router)
app.include_router(card_scans.router)
app.include_router(coverages.router)
app.include_router(dashboard.router)
app.include_router(demo.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
