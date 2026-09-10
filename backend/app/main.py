from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import (
    appeals,
    appointments,
    card_scans,
    claims,
    cost_estimates,
    coverages,
    dashboard,
    demo,
    insights,
    me,
    organizations,
    prior_auth,
    voice_automation,
    voice_reminders,
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
app.include_router(appeals.router)
app.include_router(appointments.router)
app.include_router(prior_auth.router)
app.include_router(card_scans.router)
app.include_router(coverages.router)
app.include_router(cost_estimates.router)
app.include_router(dashboard.router)
app.include_router(demo.router)
app.include_router(insights.router)
app.include_router(voice_reminders.router)
app.include_router(voice_automation.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
