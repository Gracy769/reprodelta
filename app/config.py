"""Application configuration loaded from environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://replay:replay@db:5432/replay"

    # Artifact storage
    artifact_dir: str = "/data/artifacts"

    # Nebius / LLM — optional; None means mock mode
    nebius_api_key: str | None = None
    nebius_model_id: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"
    nebius_call_cost_ceiling_usd: float = 0.0
    nebius_check_model_on_startup: bool = False

    # Application security
    secret_key: str = "change-me-in-production"
    neon_auth_base_url: str | None = None
    neon_auth_jwks_url: str | None = None
    app_url: str | None = None
    auth_allowed_origin: str | None = None
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    gitlab_oauth_client_id: str | None = None
    gitlab_oauth_client_secret: str | None = None
    firebase_api_key: str | None = None
    firebase_auth_domain: str | None = None
    firebase_project_id: str | None = None
    firebase_app_id: str | None = None

    # Logging
    log_level: str = "INFO"

    # Application
    app_version: str = "0.1.0"

    # Runner
    runner_mode: str = "local"
    worker_old_url: str = "http://worker-old:9000/run"
    worker_new_url: str = "http://worker-new:9000/run"

    # Persistence — set to True to persist investigations to ARTIFACT_DIR
    persist_investigations: bool = False
    investigation_store: str = "auto"
    max_active_investigations: int = Field(default=2, ge=1, le=8)
    guest_daily_investigations: int = Field(default=10, ge=1, le=100)
    owner_daily_investigations: int = Field(default=20, ge=1, le=1000)
    global_daily_investigations: int = Field(default=200, ge=1, le=10000)
    profile_approver_subjects: str = ""
    project_snapshot_key: str | None = None
    e2b_project_runs_enabled: bool = False
    e2b_profile_validation_enabled: bool = False
    e2b_sandbox_usd_per_second: float = Field(default=0.0, ge=0.0)
    e2b_project_run_cost_ceiling_usd: float = Field(default=0.0, ge=0.0, le=100.0)
    project_sandbox_provider: Literal["e2b", "oracle_docker"] = "e2b"
    oracle_project_runs_enabled: bool = False
    oracle_profile_validation_enabled: bool = False
    oracle_sandbox_usd_per_second: float = Field(default=0.0, ge=0.0)
    oracle_project_run_cost_ceiling_usd: float = Field(default=0.0, ge=0.0, le=100.0)

    @property
    def project_runs_enabled(self) -> bool:
        return (self.oracle_project_runs_enabled if self.project_sandbox_provider == "oracle_docker"
                else self.e2b_project_runs_enabled)

    @property
    def profile_validation_enabled(self) -> bool:
        return (self.oracle_profile_validation_enabled
                if self.project_sandbox_provider == "oracle_docker"
                else self.e2b_profile_validation_enabled)

    @property
    def project_sandbox_rate(self) -> float:
        return (self.oracle_sandbox_usd_per_second if self.project_sandbox_provider == "oracle_docker"
                else self.e2b_sandbox_usd_per_second)

    @property
    def project_cost_ceiling(self) -> float:
        return (self.oracle_project_run_cost_ceiling_usd
                if self.project_sandbox_provider == "oracle_docker"
                else self.e2b_project_run_cost_ceiling_usd)

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def mock_llm(self) -> bool:
        """True when no Nebius API key is configured."""
        return not bool(self.nebius_api_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
