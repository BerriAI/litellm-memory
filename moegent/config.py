from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "/etc/secrets/moegent.env", "/etc/secrets/litemem.env"), extra="ignore")

    litellm_base_url: str = "https://gateway.litellm-sandbox.ai/v1"
    litellm_api_key: str = ""
    litellm_model: str = "gpt-5.6-luna-priority"
    litellm_search_tool: str = ""
    background_model: str = "gpt-5.6-luna-priority"
    slack_model: str = ""
    model_config_revision: int = 1
    jev_path: str = "/typesafe/v1/systemone"
    jev_model: str = "jev-latest"
    jev_concurrency: int = Field(default=8, ge=1, le=64)
    model_catalog_ttl: int = Field(default=300, ge=0)
    registration_mode: Literal["owner_only", "domain"] = "domain"
    allowed_email_domain: str = "berri.ai"
    sharing_enabled: bool = False
    ente_base_url: str = ""
    ente_auth_token: str = ""
    vault_wrapping_key: str = ""
    public_base_url: str = "http://localhost:8000"
    access_mode: Literal["public", "allowlist"] = "public"
    allowed_slack_users: str = ""  # comma-separated TEAM_ID:USER_ID pairs
    session_secret: str = ""
    setup_code: str = ""
    state_dir: Path = ROOT / ".state"
    memory_repo: str = "BerriAI/product-team-knowledge"
    memory_allowed_actors: str = Field(default="moe-berri", min_length=1)
    memory_script: Path = ROOT / "vendor/product-team-knowledge/bin/memory.mjs"
    memory_embedding_model: str = "gemini/gemini-embedding-001"
    memory_embedding_version: str = "1"
    memory_warm_on_start: bool = True
    gh_token: str = ""
    slack_team_id: str = ""
    slack_owner_user_id: str = ""
    slack_app_id: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""
    slack_bot_token: str = ""
    slack_bot_user_id: str = ""
    max_agent_turns: int = Field(default=18, ge=1)
    agent_timeout_seconds: int = Field(default=300, ge=10)

    @property
    def memory_state_dir(self) -> Path:
        return self.state_dir / "memory"

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https://")
