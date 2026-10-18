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

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        # The workspace .env is the source of truth. Shell variables only fill settings the
        # .env does not define, so a stale exported GEMINI_API_KEY / NEO4J_* cannot silently
        # replace the configured values (see shadowed_env_names for diagnostics).
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    # Neo4j
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    # Gemini (native Generative Language API) LLM endpoint
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str | None = None  # default: https://generativelanguage.googleapis.com/v1beta
    gemini_model: str = "gemini-3.8-flash"

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
    llm_max_retries: int = Field(default=1, ge=0, le=3)  # only for 429 / 5xx / timeouts / network

    # Remote / uploaded repository sources (GitHub URL, ZIP upload). Public GitHub only:
    # there is deliberately no token setting.
    codeatlas_tmp_dir: str | None = None  # parent dir for temporary checkouts; None = system temp
    max_archive_bytes: int = Field(default=200_000_000, ge=1)  # upload / download size cap
    max_extracted_bytes: int = Field(default=600_000_000, ge=1)  # total uncompressed size cap
    max_archive_entries: int = Field(default=20_000, ge=1)
    max_compression_ratio: float = Field(default=200.0, gt=1)
    compression_ratio_min_bytes: int = 1_000_000  # ratio check applies to entries above this
    github_timeout_s: float = 120.0
    # Optional, server-side only, never sent to the browser. Without it GitHub allows 60 anonymous
    # API requests per hour per IP. A read-only token raises that; it is sent only to api.github.com.
    github_token: SecretStr | None = None
    github_connect_timeout_s: float = 10.0

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value())

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_password.get_secret_value())


def shadowed_env_names(env_file: Path | None = ROOT_ENV_FILE) -> list[str]:
    """Names (never values) of variables exported in the shell with a value that differs from
    the .env file. The .env value wins; this only makes the conflict visible."""
    if not env_file or not Path(env_file).is_file():
        return []
    from dotenv import dotenv_values  # dependency of pydantic-settings

    file_values = dotenv_values(env_file)
    env = {k.upper(): v for k, v in os.environ.items()}
    return sorted(k for k, v in file_values.items()
                  if v is not None and k.upper() in env and env[k.upper()] != v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
