"""Opt-in Aura integration tests (WRITE to Neo4j). Run only with approval:

    pytest -m neo4j

They create projects whose ids start with `test-` and delete only those projects.
"""

import shutil
import uuid

import pytest

from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.config import get_settings
from codeatlas.graph import db, queries, sync

from .conftest import FIXTURE

pytestmark = pytest.mark.neo4j


@pytest.fixture
def settings():
    s = get_settings()
    if not s.neo4j_configured:
        pytest.skip("Neo4j not configured")
    return s


@pytest.fixture
def repo(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(FIXTURE, dst)
    return dst


@pytest.fixture
def created(settings):
    pids: list[str] = []
    yield pids
    for pid in pids:
        assert pid.startswith("test-")  # never delete anything else
        sync.delete_project(pid, settings)
    db.close_driver()


def _analyze(repo, settings, created, name):
    out = analyze_repository(str(repo), settings, name=name)
    if out.project_id not in created:
        created.append(out.project_id)
    return out


def _counts(pid):
    st = queries.stats(pid)
    return st["nodes"], st["relationships"]


def test_ingest_is_idempotent_and_consistent(repo, settings, created):
    name = f"test-{uuid.uuid4().hex[:6]}"
    out = _analyze(repo, settings, created, name)
    rep1 = sync.sync_project(out, settings)
    nodes1, rels1 = _counts(out.project_id)
    assert nodes1 == rep1.node_counts and rels1 == rep1.rel_counts

    rep2 = sync.sync_project(_analyze(repo, settings, created, name), settings)
    assert _counts(out.project_id) == (nodes1, rels1)
    assert (rep2.swept_nodes, rep2.swept_rels) == (0, 0)
    assert all(v == 0 for v in queries.integrity(out.project_id).values())
    assert queries.project(out.project_id)["sync_status"] == "complete"


def test_removed_files_are_swept_without_touching_other_projects(repo, settings, created, tmp_path):
    other_repo = tmp_path / "other"
    shutil.copytree(FIXTURE, other_repo)
    other = _analyze(other_repo, settings, created, f"test-{uuid.uuid4().hex[:6]}")
    sync.sync_project(other, settings)
    other_counts = _counts(other.project_id)

    name = f"test-{uuid.uuid4().hex[:6]}"
    sync.sync_project(_analyze(repo, settings, created, name), settings)
    (repo / "dup_b.py").unlink()
    (repo / "shop" / "utils.py").write_text("def fmt(value):\n    return str(value)\n")
    out = _analyze(repo, settings, created, name)
    rep = sync.sync_project(out, settings)
    assert rep.swept_nodes >= 3  # dup_b.py file + process() + utils.helper()
    nodes, _ = _counts(out.project_id)
    assert nodes == rep.node_counts
    paths = {f["path"] for f in queries.list_files(out.project_id)}
    assert "dup_b.py" not in paths
    assert _counts(other.project_id) == other_counts


def test_failed_write_is_flagged_and_retry_converges(repo, settings, created, monkeypatch):
    name = f"test-{uuid.uuid4().hex[:6]}"
    out = _analyze(repo, settings, created, name)
    real_write, calls = db.write, {"n": 0}

    def flaky(query, params=None, timeout=None):
        if "MERGE (a)-[r:CALLS]" in query:
            calls["n"] += 1
            raise RuntimeError("simulated failure after node batches")
        return real_write(query, params, timeout)

    monkeypatch.setattr(sync.db, "write", flaky)
    with pytest.raises(RuntimeError):
        sync.sync_project(out, settings)
    monkeypatch.setattr(sync.db, "write", real_write)
    assert queries.project(out.project_id)["sync_status"] == "failed"

    rep = sync.sync_project(_analyze(repo, settings, created, name), settings)
    assert queries.project(out.project_id)["sync_status"] == "complete"
    assert _counts(out.project_id) == (rep.node_counts, rep.rel_counts)


def test_fixture_queries_return_expected_graphs(repo, settings, created):
    out = _analyze(repo, settings, created, f"test-{uuid.uuid4().hex[:6]}")
    sync.sync_project(out, settings)
    pid = out.project_id
    fid = lambda p: f"{pid}:file:{p}"  # noqa: E731

    overview = queries.overview(pid, limit=400)
    assert len(overview.nodes) == out.rows.node_count()
    for label, rows in [("Project", [out.rows.project])] + list(out.rows.nodes.items()):
        g = queries.neighborhood(label, rows[0].id, depth=1)
        assert any(n.id == rows[0].id for n in g.nodes), label
        assert g.edges, f"{label} neighbourhood has no relationships"

    path = queries.path_between("dependency", fid("shop/cli.py"), fid("shop/models.py"))
    assert [(e.source, e.target) for e in path.edges] == [(fid("shop/cli.py"), fid("shop/models.py"))]

    imp = queries.impact("File", fid("shop/models.py"), depth=2)
    assert {r["file"] for r in imp["importing_files"]} == {"shop/cli.py", "shop/pricing.py"}
    assert {(r["file"], r["function"]) for r in imp["calling_functions"]} >= {
        ("shop/pricing.py", "make_items.build"), ("shop/cli.py", "main")}
    utils_impact = queries.impact("Function", f"{pid}:function:shop/utils.py:fmt", depth=1)
    assert {r["function"] for r in utils_impact["calling_functions"]} == {"main", "total"}
