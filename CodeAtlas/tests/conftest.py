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


def build_zip(path: Path, entries: dict[str, bytes | str], wrapper: str | None = None,
              compression: int = 8) -> Path:
    """Write a ZIP at `path`; names are prefixed with `wrapper/` when given."""
    import zipfile

    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(f"{wrapper}/{name}" if wrapper else name, data)
    return path


def fixture_zip(path: Path, wrapper: str | None = None) -> Path:
    """The sample_repo fixture (Python files only, like a GitHub archive) as a ZIP."""
    entries = {p.relative_to(FIXTURE).as_posix(): p.read_bytes()
               for p in sorted(FIXTURE.rglob("*.py"))
               if "node_modules" not in p.parts and ".venv" not in p.parts}
    entries["README.md"] = "# sample\n"
    return build_zip(path, entries, wrapper)
