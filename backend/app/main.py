from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import claims, me, organizations

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


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
