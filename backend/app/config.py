from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env — resolved absolutely so it loads no matter the working directory
# (uvicorn from backend/, the seed script from the repo root, tests from tests/).
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    supabase_url: str = "http://127.0.0.1:54321"
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = "super-secret-jwt-token-with-at-least-32-characters-long"
    # Used ONLY by the background agent system (app/agents/*), never to answer a
    # user request. See docs/architecture.md §3.4.
    supabase_service_role_key: str = ""

    # 07-reasoning-agent calls the Claude API. Key comes from the environment
    # (ANTHROPIC_API_KEY) — never hardcoded. Absent -> the reasoning step raises
    # and the Commander escalates (R5).
    anthropic_api_key: str = ""
    reasoning_model: str = "claude-opus-5"

    # 03-ocr-agent calls Claude's vision capability (same ANTHROPIC_API_KEY).
    # Absent -> POST /api/card-scans records the scan with status 'error' and
    # surfaces it; nothing 5xxs and no field is ever guessed.
    ocr_model: str = "claude-opus-5"
    # Confidence floor below which a field forces needs_review. One of
    # high | medium | low — see app/agents/ocr.classify_extraction.
    ocr_confidence_floor: str = "medium"

    # 05-cost-estimate-agent: the AI only PHRASES an already-computed Good Faith
    # Estimate (it never touches a number). Absent / API error -> the estimate is
    # still produced with a deterministic plain-language template.
    cost_estimate_model: str = "claude-opus-5"

    # 11-appeals-agent: the AI DRAFTS an appeal letter grounded strictly in
    # evidence already in the system (11-appeals-agent.md §2.1). Absent / API
    # error -> the appeal is recorded 'error' and the Commander escalates (AP11);
    # there is no hollow fallback template (07's discipline).
    appeals_model: str = "claude-opus-5"

    frontend_origin: str = "http://localhost:5173"

    # Supabase access tokens are minted with this audience.
    jwt_audience: str = "authenticated"

    @property
    def rest_url(self) -> str:
        # Trailing slash matters: httpx resolves request paths relative to this.
        return f"{self.supabase_url.rstrip('/')}/rest/v1/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
