from pathlib import Path

import pytest

from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def offline_settings() -> Settings:
    # never read the real .env in unit tests
    return Settings(_env_file=None, neo4j_uri="", gemini_api_key=None)


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


# An empty (but well-formed) /projects/{pid}/architecture response, for UI tests whose request
# stubs are about other features. Phase 2 UI behaviour is tested in test_intel_api.py.
EMPTY_ARCHITECTURE = {"project_id": "x", "layers": [], "components": [], "files": [],
                      "layer_edges": [], "violations": [], "component_cycles": [],
                      "file_cycles": [], "entry_points": [], "notes": []}


LAYERED_REPO = {
    "app/__init__.py": "",
    "app/api/__init__.py": "",
    "app/api/routes.py": '''"""HTTP routes for orders."""
from fastapi import APIRouter

from app.services.orders import place_order

router = APIRouter()


@router.post("/orders")
def create_order(payload):
    """HTTP endpoint that places an order."""
    return place_order(payload)
''',
    "app/services/__init__.py": "",
    "app/services/orders.py": '''"""Order business rules."""
from app.db.repo import save_order
from app.models.order import Order


def place_order(payload):
    """Validate the payload and persist an order."""
    order = Order(**payload)
    validate_order(order)
    save_order(order)
    return order


def validate_order(order):
    """Check that the order total is positive."""
    if order.total <= 0:
        raise ValueError("total must be positive")
''',
    "app/models/__init__.py": "",
    "app/models/order.py": '''"""Domain model."""
from pydantic import BaseModel


class Order(BaseModel):
    """An order placed by a customer."""
    total: float

    def describe(self):
        return f"order of {self.total}"
''',
    "app/db/__init__.py": "",
    "app/db/repo.py": '''"""Persistence."""
import sqlite3

from app.services.orders import validate_order


def save_order(order):
    """Insert the order into the database."""
    validate_order(order)
    conn = sqlite3.connect("orders.db")
    conn.execute("insert into orders values (?)", (order.total,))
    return conn
''',
    "app/utils/__init__.py": "",
    "app/utils/fmt.py": '''def money(x):
    return f"{x:.2f}"
''',
    "app/stuff/__init__.py": "",
    "app/stuff/thing.py": '''def frobnicate(x):
    return x
''',
    "tests/test_orders.py": '''import pytest

from app.services.orders import place_order


def test_place_order():
    with pytest.raises(ValueError):
        place_order({"total": 0})
''',
}


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def snapshot_of(path, settings, name=None):
    from codeatlas.intel.snapshot import Snapshot

    out = analyze_repository(str(path), settings, name)
    return Snapshot.from_rows(out.project_id, out.project_name, out.rows)
