from pathlib import Path

import pytest

from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def offline_settings() -> Settings:
    # never read the real .env in unit tests
    return Settings(_env_file=None, neo4j_uri="", openai_api_key=None)


@pytest.fixture
def fixture_output(offline_settings):
    return analyze_repository(str(FIXTURE), offline_settings)


def short(identifier: str) -> str:
    """Drop the project-id prefix for readable assertions."""
    return identifier.split(":", 1)[1] if ":" in identifier else "<project>"
