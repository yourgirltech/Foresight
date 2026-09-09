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

    # Phase 6 — voice appointment reminders (agent 17, an n8n workflow).
    # n8n calls /api/automation/voice-reminders/* with this bearer token. It is
    # NOT a Supabase role and is accepted on no other router. Absent -> every
    # automation endpoint returns 503 (the automation surface is closed).
    n8n_service_token: str = ""            # N8N_SERVICE_TOKEN
    # Echoed to n8n in the /due payload so the workflow does not hardcode them.
    vapi_assistant_id: str = ""            # VAPI_ASSISTANT_ID
    vapi_phone_number_id: str = ""         # VAPI_PHONE_NUMBER_ID
    # The Vapi Server URL Secret. n8n's Webhook node verifies it; we keep it here
    # only for parity / future server-side verification.
    vapi_webhook_secret: str = ""          # VAPI_WEBHOOK_SECRET
    voice_reminder_lead_hours: int = 24    # place the call this many hours before the appointment
    voice_dispatch_lease_minutes: int = 15 # a 'dispatching' row past this with no mark-calling -> lost-dispatch sweep
    voice_due_batch_limit: int = 50        # max reminders returned by one /due poll
    voice_due_stale_hours: int = 48        # a reminder this far past scheduled_call_at -> error + escalation, not a late call

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
