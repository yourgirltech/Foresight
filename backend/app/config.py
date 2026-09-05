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
