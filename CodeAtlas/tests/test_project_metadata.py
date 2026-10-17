"""Projects with and without newer source metadata (offline, faked db.read/db.write).

- Read queries never name optional Project keys, so older databases do not emit
  "property key does not exist" notices (sync_error exists only after a failed sync,
  source_* only after a sync with source tracking).
- Source metadata survives sync begin / success / failure, and CLI (local) runs keep it.
"""

import re

import pytest

from codeatlas import cli
from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.graph import queries, sync
from codeatlas.models import SourceTarget

from .conftest import FIXTURE

OPTIONAL_KEYS = ("sync_error", "sync_failed_phase", "source_kind", "source_label",
                 "source_identity", "source_ref")
READ_QUERIES = {
    "LIST_PROJECTS": queries.LIST_PROJECTS, "PROJECT": queries.PROJECT,
    "PROJECTS_BY_IDENTITY": queries.PROJECTS_BY_IDENTITY, "NODE_STATS": queries.NODE_STATS,
    "FIND_ENTITIES": queries.FIND_ENTITIES,
    "overview": queries.overview_query(list(queries.LABELS)),
    "rel_variants": queries._rel_variant_counts_query(),
}


@pytest.mark.parametrize("name", sorted(READ_QUERIES))
def test_read_queries_do_not_name_optional_project_keys(name):
    for key in OPTIONAL_KEYS:
        assert not re.search(rf"\.\s*{key}\b", READ_QUERIES[name]), (name, key)


OLD = {"id": "sample-repo-dc690514", "name": "sample_repo", "source": "codeatlas",
       "root_path": "/x/sample_repo", "sync_status": "complete",
       "synced_at": "2026-10-16T10:00:00+00:00", "file_count": 8, "pending_run_id": None}
NEW = {"id": "charlie-069e9d6b", "name": "charlie", "source": "codeatlas",
       "root_path": "github.com/o/charlie@main", "sync_status": "complete",
       "synced_at": "2026-10-17T10:00:00+00:00", "file_count": 30, "source_kind": "github",
       "source_label": "github.com/o/charlie@main", "source_identity": "github:o/charlie@main",
       "source_ref": "main", "sync_heartbeat": 1}
FAILED = {**OLD, "id": "broken-1", "sync_status": "failed", "sync_error": "boom"}


def test_list_projects_with_and_without_source_metadata(monkeypatch):
    monkeypatch.setattr(queries.db, "read", lambda q, p=None: [
        {"props": OLD}, {"props": NEW}, {"props": FAILED}])
    rows = queries.list_projects()
    assert [r["id"] for r in rows] == ["sample-repo-dc690514", "charlie-069e9d6b", "broken-1"]
    assert all(set(r) == set(queries.PROJECT_LIST_FIELDS) for r in rows)  # same shape
    assert rows[0]["source_kind"] is None and rows[0]["sync_error"] is None
    assert rows[1]["source_kind"] == "github" and rows[1]["source_label"].startswith("github")
    assert rows[2]["sync_error"] == "boom"
    assert "sync_heartbeat" not in rows[1]  # only the listed fields


def test_identity_lookup_semantics(monkeypatch):
    seen = {}

    def read(q, p=None):
        seen.update(query=q, params=p)
        return [{"id": row["id"]} for row in (OLD, NEW)
                if row.get(p["identity_key"]) == p["identity"]]  # p[$identity_key] = $identity
    monkeypatch.setattr(queries.db, "read", read)
    assert queries.projects_by_identity("github:o/charlie@main") == ["charlie-069e9d6b"]
    assert queries.projects_by_identity("local:/x/sample_repo") == []  # old project: no key
    assert seen["params"]["identity_key"] == "source_identity"
    assert "p[$identity_key] = $identity" in seen["query"]


# ------------------------------------------------------------------ sync metadata lifecycle

class FakeWrites:
    def __init__(self, fail_on: str | None = None):
        self.calls, self.fail_on = [], fail_on

    def write(self, query, params=None):
        self.calls.append((query, params or {}))
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("simulated write failure")
        if query == sync.BEGIN_QUERY:
            return [{"busy": False, "acquired": True}]
        if "UNWIND $rows" in query:
            return [{"written": len(params["rows"])}]
        if "DELETE" in query:
            return [{"deleted": 0}]
        return [{"ok": 1}]

    def params_of(self, query):
        return [p for q, p in self.calls if q == query]


@pytest.fixture
def fake_db(monkeypatch, offline_settings):
    monkeypatch.setattr(sync.schema, "ensure_schema", lambda: None)
    monkeypatch.setattr(sync.db, "read", lambda q, p=None: [{"n": 0}])  # capacity totals

    def install(fail_on=None):
        fw = FakeWrites(fail_on)
        monkeypatch.setattr(sync.db, "write", fw.write)
        return fw
    return install


def _source(out):
    return SourceTarget(kind="github", label="github.com/o/charlie@main",
                        identity="github:o/charlie@main", project_id=out.project_id,
                        project_name=out.project_name, ref="main")


def test_cli_style_sync_does_not_overwrite_source_metadata(fake_db, offline_settings):
    fw = fake_db()
    out = analyze_repository(str(FIXTURE), offline_settings)  # no source: CLI path
    sync.sync_project(out, offline_settings)
    begin = fw.params_of(sync.BEGIN_QUERY)[0]
    assert begin["source_kind"] is begin["source_identity"] is begin["source_label"] is None
    assert "coalesce($source_identity, p.source_identity)" in sync.BEGIN_QUERY  # keeps existing
    final = fw.params_of(sync.FINALIZE_QUERY)[0]["props"]
    assert not any(k.startswith("source_") for k in final)
    assert final["root_path"] == out.scan.root  # local behaviour unchanged


def test_source_sync_sets_metadata_at_begin_and_finalize(fake_db, offline_settings):
    fw = fake_db()
    out = analyze_repository(str(FIXTURE), offline_settings)
    out = analyze_repository(str(FIXTURE), offline_settings, source=_source(out))
    sync.sync_project(out, offline_settings)
    begin = fw.params_of(sync.BEGIN_QUERY)[0]
    assert (begin["source_kind"], begin["source_identity"]) == ("github", "github:o/charlie@main")
    assert begin["root"] == "github.com/o/charlie@main"
    final = fw.params_of(sync.FINALIZE_QUERY)[0]["props"]
    assert final["source_identity"] == "github:o/charlie@main" and final["source_ref"] == "main"


def test_failed_sync_keeps_source_metadata(fake_db, offline_settings):
    fw = fake_db(fail_on="MERGE (n:Function")
    out = analyze_repository(str(FIXTURE), offline_settings)
    out = analyze_repository(str(FIXTURE), offline_settings, source=_source(out))
    with pytest.raises(RuntimeError, match="simulated"):
        sync.sync_project(out, offline_settings)
    fail = fw.params_of(sync.FAIL_QUERY)
    assert len(fail) == 1 and fail[0]["phase"] == "write"
    assert not re.search(r"source_\w+", sync.FAIL_QUERY)  # failure never touches source_*
    assert fw.params_of(sync.FINALIZE_QUERY) == []  # no sweep/finalize after a failure
    assert not any("DELETE" in q for q, _ in fw.calls)


def test_cli_dry_run_unchanged(capsys, monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("dry run touched Neo4j")
    monkeypatch.setattr(sync.db, "write", no_db)
    monkeypatch.setattr(sync.db, "read", no_db)
    assert cli.main(["analyze", str(FIXTURE), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "sample-repo" in out and "dry_run" in out
