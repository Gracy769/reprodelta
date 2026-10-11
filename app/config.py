"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import field_validator
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
    nebius_model_id: str = "nvidia/Nemotron-3_5-Lightning"
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"
    nebius_check_model_on_startup: bool = False

    # Application security
    secret_key: str = "change-me-in-production"

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
