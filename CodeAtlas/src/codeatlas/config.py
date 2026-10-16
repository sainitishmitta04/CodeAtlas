"""Validated settings loaded from the workspace `.env` (see find_env_file); never printed."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# CodeAtlas/src/codeatlas/config.py
APP_ROOT = Path(__file__).resolve().parents[2]  # CodeAtlas/
WORKSPACE_ROOT = APP_ROOT.parent  # folder holding CodeAtlas/, the shared .venv and .env


def find_env_file() -> Path | None:
    """CODEATLAS_ENV_FILE if set, else CodeAtlas/.env, else the workspace-root .env."""
    override = os.environ.get("CODEATLAS_ENV_FILE")
    if override:
        return Path(override).expanduser()
    for candidate in (APP_ROOT / ".env", WORKSPACE_ROOT / ".env"):
        if candidate.is_file():
            return candidate
    return None


ROOT_ENV_FILE = find_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    # Neo4j
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    # OpenAI-compatible LLM endpoint
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"

    app_env: str = "dev"
    log_level: str = "INFO"

    # CodeAtlas limits (sized conservatively for AuraDB Free)
    codeatlas_api_url: str = "http://127.0.0.1:8000"
    max_files: int = 1500
    max_file_bytes: int = 300_000
    max_project_nodes: int = 25_000
    max_project_rels: int = 60_000
    aura_node_limit: int = 200_000
    aura_rel_limit: int = 400_000
    write_batch_size: int = Field(default=500, ge=1, le=5000)
    sweep_chunk_size: int = 1000
    query_timeout_s: float = 30.0
    write_timeout_s: float = 60.0
    llm_timeout_s: float = 45.0

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value())

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_password.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
