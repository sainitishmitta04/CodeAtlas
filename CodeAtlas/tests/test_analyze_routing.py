"""All three sources go through the same analyzer + write guard; dry runs never sync.

Neo4j is replaced by an in-memory project store, `sync_project` by a recorder, and GitHub
by an httpx.MockTransport. Any real database call fails the test.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from codeatlas import ids
from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.api import main
from codeatlas.graph import db
from codeatlas.graph.db import GraphDBError
from codeatlas.graph.sync import dry_run_report
from codeatlas.sources import github, service

from .conftest import EMPTY_ARCHITECTURE, FIXTURE, build_zip, fixture_zip


class FakeGraph:
    """Just enough of the Project lookups for the write guard."""

    def __init__(self):
        self.projects: dict[str, dict] = {}
        self.synced: list = []
        self.down = False

    def project(self, pid):
        if self.down:
            raise GraphDBError("Neo4j is unreachable.")
        return self.projects.get(pid)

    def projects_by_identity(self, identity):
        if self.down:
            raise GraphDBError("Neo4j is unreachable.")
        return [p for p, v in self.projects.items() if v.get("source_identity") == identity]

    def sync(self, out, settings):
        self.synced.append(out)
        s = out.source
        self.projects[out.project_id] = {
            "id": out.project_id, "name": out.project_name, "source": "codeatlas",
            "sync_status": "complete", "synced_at": "2026-10-17T00:00:00+00:00",
            "root_path": out.root_label, "source_kind": s.kind if s else None,
            "source_identity": s.identity if s else None}
        rep = dry_run_report(out)
        rep.dry_run, rep.sync_status, rep.run_id = False, "complete", "test-run"
        return rep


@pytest.fixture
def env(monkeypatch, tmp_path, offline_settings):
    settings = offline_settings.model_copy(update={"codeatlas_tmp_dir": str(tmp_path / "work")})
    fake = FakeGraph()
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(service.queries, "project", fake.project)
    monkeypatch.setattr(service.queries, "projects_by_identity", fake.projects_by_identity)
    monkeypatch.setattr(service, "sync_project", fake.sync)

    def no_db(*a, **k):
        raise AssertionError("offline test touched Neo4j")
    monkeypatch.setattr(db, "read", no_db)
    monkeypatch.setattr(db, "write", no_db)
    fake.github_calls = []
    archive = fixture_zip(tmp_path / "gh.zip", wrapper="Repo-abc123").read_bytes()

    def gh(req: httpx.Request) -> httpx.Response:
        fake.github_calls.append(str(req.url))
        if req.url.path.lower() == "/repos/owner/repo":
            return httpx.Response(200, json={"full_name": "Owner/Repo", "private": False,
                                             "default_branch": "main"})
        if "/zipball/" in req.url.path:
            if req.url.path.endswith("/missing"):
                return httpx.Response(404)
            return httpx.Response(302, headers={"location": "https://codeload.github.com/z"})
        if req.url.host == "codeload.github.com":
            return httpx.Response(200, content=archive)
        return httpx.Response(404)
    monkeypatch.setattr(github, "make_client", lambda s: httpx.Client(
        transport=httpx.MockTransport(gh), headers=github.HEADERS))
    fake.settings, fake.work = settings, tmp_path / "work"
    return fake


@pytest.fixture
def client(env):
    return TestClient(main.app)


def leftovers(env) -> list:
    return list(env.work.iterdir()) if env.work.exists() else []


def write_with_confirmation(client, path, body=None, params=None, content=None, **kw):
    """Simulate the UI: first write attempt returns the target, the second confirms it."""
    call = (lambda extra: client.post(path, json={**body, **extra}) if body is not None else
            client.post(path, params={**params, **extra}, content=content,
                        headers={"Content-Type": "application/zip"}))
    first = call({"dry_run": False})
    assert first.status_code == 409, first.text
    detail = first.json()["detail"]
    assert detail["code"] == "confirmation_required"
    target = detail["target"]
    second = call({"dry_run": False, "expected_project_id": target["project_id"],
                   "confirm_update": bool(target["exists"])})
    return target, second


# ------------------------------------------------------------------ local directory

def test_local_project_id_formula_unchanged(env, client):
    r = client.post("/projects/analyze", json={"path": str(FIXTURE), "dry_run": True})
    assert r.status_code == 200, r.text
    rep = r.json()
    legacy = analyze_repository(str(FIXTURE), env.settings)  # the original code path
    assert rep["project_id"] == legacy.project_id == ids.project_id_for(str(FIXTURE))
    assert rep["project_name"] == legacy.project_name == "sample_repo"
    assert rep["root_path"] == legacy.scan.root
    assert rep["node_counts"] == dry_run_report(legacy).node_counts
    assert rep["dry_run"] and env.synced == []
    named = client.post("/projects/analyze", json={"path": str(FIXTURE), "name": "Shop",
                                                   "dry_run": True}).json()
    assert named["project_id"] == ids.project_id_for(str(FIXTURE), "Shop")


def test_local_write_requires_target_confirmation(env, client):
    target, r = write_with_confirmation(client, "/projects/analyze", body={"path": str(FIXTURE)})
    assert target["exists"] is False and target["kind"] == "local"
    assert r.status_code == 200 and r.json()["sync_status"] == "complete"
    assert len(env.synced) == 1


def test_existing_project_needs_explicit_update_confirmation(env, client):
    pid = ids.project_id_for(str(FIXTURE))
    env.projects[pid] = {"id": pid, "name": "sample_repo", "source": "codeatlas",
                         "sync_status": "complete"}  # legacy local project (no identity)
    body = {"path": str(FIXTURE), "dry_run": False, "expected_project_id": pid}
    r = client.post("/projects/analyze", json=body)
    assert r.status_code == 409 and r.json()["detail"]["target"]["exists"] is True
    assert env.synced == []
    r = client.post("/projects/analyze", json={**body, "confirm_update": True})
    assert r.status_code == 200 and len(env.synced) == 1


def test_wrong_expected_project_is_refused(env, client):
    r = client.post("/projects/analyze", json={"path": str(FIXTURE), "dry_run": False,
                                               "expected_project_id": "other-1234"})
    assert r.status_code == 409 and env.synced == []


def test_local_errors(client):
    r = client.post("/projects/analyze", json={"path": "/definitely/not/here", "dry_run": True})
    assert r.status_code == 400 and "Not a directory" in r.json()["detail"]


def test_dry_run_survives_unreachable_neo4j_but_write_does_not(env, client):
    env.down = True
    r = client.post("/projects/analyze", json={"path": str(FIXTURE), "dry_run": True})
    assert r.status_code == 200 and r.json()["source"]["exists"] is None
    r = client.post("/projects/analyze", json={"path": str(FIXTURE), "dry_run": False})
    assert r.status_code == 503 and env.synced == []


# ------------------------------------------------------------------ GitHub

def test_github_dry_run_routes_through_shared_pipeline(env, client):
    r = client.post("/projects/analyze/github",
                    json={"url": "https://github.com/owner/repo", "dry_run": True})
    assert r.status_code == 200, r.text
    rep = r.json()
    src = rep["source"]
    assert src["identity"] == "github:owner/repo@main" and src["ref_is_default"] is True
    assert rep["root_path"] == "github.com/Owner/Repo@main"  # never the temporary path
    assert rep["project_id"] == ids.project_id_for_identity("github:owner/repo@main", "Repo")
    local = dry_run_report(analyze_repository(str(FIXTURE), env.settings))
    assert rep["node_counts"] == local.node_counts and rep["rel_counts"] == local.rel_counts
    assert env.synced == [] and leftovers(env) == []


def test_github_default_branch_and_explicit_name_do_not_duplicate(env, client):
    _, r = write_with_confirmation(client, "/projects/analyze/github",
                                   body={"url": "https://github.com/owner/repo"})
    first = r.json()
    assert first["sync_status"] == "complete"
    assert env.synced[0].rows.project.props["root_path"] == "github.com/Owner/Repo@main"
    again = client.post("/projects/analyze/github", json={
        "url": "https://github.com/OWNER/repo.git", "ref": "main", "name": "Renamed",
        "dry_run": True}).json()
    assert again["project_id"] == first["project_id"] and again["source"]["exists"] is True


def test_github_write_guard_runs_before_download(env, client):
    r = client.post("/projects/analyze/github",
                    json={"url": "https://github.com/owner/repo", "dry_run": False})
    assert r.status_code == 409
    assert not any("zipball" in u or "codeload" in u for u in env.github_calls)


def test_github_invalid_url_makes_no_request(env, client):
    r = client.post("/projects/analyze/github",
                    json={"url": "https://github.com.evil.io/owner/repo", "dry_run": True})
    assert r.status_code == 400 and env.github_calls == []


def test_github_missing_ref_cleans_up(env, client):
    r = client.post("/projects/analyze/github", json={
        "url": "https://github.com/owner/repo", "ref": "missing", "dry_run": True})
    assert r.status_code == 404 and "missing" in r.json()["detail"]
    assert leftovers(env) == []


def test_github_cleanup_when_analysis_fails(env, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("analyzer crashed")
    monkeypatch.setattr(service, "analyze_repository", boom)
    with pytest.raises(RuntimeError):
        client.post("/projects/analyze/github",
                    json={"url": "https://github.com/owner/repo", "dry_run": True})
    assert leftovers(env) == []


# ------------------------------------------------------------------ ZIP upload

def post_zip(client, data, **params):
    return client.post("/projects/analyze/zip", params=params, content=data,
                       headers={"Content-Type": "application/zip"})


def test_zip_dry_run_flat_and_wrapped_give_same_target(env, client, tmp_path):
    flat = post_zip(client, fixture_zip(tmp_path / "f.zip").read_bytes(),
                    filename="shop.zip", dry_run=True).json()
    wrapped = post_zip(client, fixture_zip(tmp_path / "w.zip", "shop-main").read_bytes(),
                       filename="shop.zip", dry_run=True).json()
    assert flat["project_id"] == wrapped["project_id"] and flat["project_name"] == "shop"
    local = dry_run_report(analyze_repository(str(FIXTURE), env.settings))
    assert flat["node_counts"] == wrapped["node_counts"] == local.node_counts
    assert flat["rel_counts"] == local.rel_counts and flat["files_analyzed"] == 8
    assert flat["root_path"].startswith("zip:shop.zip (sha256 ")
    assert str(env.work) not in flat["root_path"]
    assert any("shop-main" in n for n in wrapped["source"]["notes"])
    assert env.synced == [] and leftovers(env) == []


def test_zip_identity_is_content_not_filename(env, client, tmp_path):
    data = fixture_zip(tmp_path / "a.zip").read_bytes()
    target, r = write_with_confirmation(client, "/projects/analyze/zip",
                                        params={"filename": "first.zip"}, content=data)
    assert r.status_code == 200 and target["exists"] is False
    # same content under another file name: resolves to the same project, needs confirmation
    again = post_zip(client, data, filename="renamed.zip", dry_run=False,
                     expected_project_id=target["project_id"])
    assert again.status_code == 409
    t2 = again.json()["detail"]["target"]
    assert t2["project_id"] == target["project_id"] and t2["exists"] is True
    assert any("already stored" in n for n in t2["notes"])
    # different content under the same file name: a separate new project
    other = post_zip(client, build_zip(tmp_path / "b.zip", {"m.py": "x = 1\n"}).read_bytes(),
                     filename="first.zip", dry_run=True).json()
    assert other["project_id"] != target["project_id"] and other["source"]["exists"] is False


def test_zip_explicit_update_keeps_project_identity(env, client, tmp_path):
    _, r = write_with_confirmation(client, "/projects/analyze/zip", params={"filename": "v1.zip"},
                                   content=fixture_zip(tmp_path / "v1.zip").read_bytes())
    pid = r.json()["project_id"]
    identity = env.projects[pid]["source_identity"]
    v2 = build_zip(tmp_path / "v2.zip", {"app.py": "def main():\n    return 1\n"}).read_bytes()
    target, r2 = write_with_confirmation(client, "/projects/analyze/zip",
                                         params={"filename": "v2.zip", "target_project_id": pid},
                                         content=v2)
    assert target["project_id"] == pid and target["exists"] is True
    assert r2.status_code == 200 and r2.json()["files_analyzed"] == 1
    assert env.projects[pid]["source_identity"] == identity and len(env.synced) == 2


def test_zip_update_refuses_non_zip_projects(env, client, tmp_path):
    env.projects["local-1"] = {"id": "local-1", "source": "codeatlas", "source_kind": "local",
                               "source_identity": "local:/x"}
    r = post_zip(client, fixture_zip(tmp_path / "z.zip").read_bytes(), filename="z.zip",
                 dry_run=True, target_project_id="local-1")
    assert r.status_code == 409 and "Only projects created from a ZIP" in r.json()["detail"]
    r = post_zip(client, fixture_zip(tmp_path / "z.zip").read_bytes(), filename="z.zip",
                 dry_run=True, target_project_id="nope")
    assert r.status_code == 404


def test_project_id_collision_with_other_source_is_refused(env, client, tmp_path):
    data = fixture_zip(tmp_path / "z.zip").read_bytes()
    pid = post_zip(client, data, filename="z.zip", dry_run=True).json()["project_id"]
    env.projects[pid] = {"id": pid, "source": "codeatlas", "source_identity": "zip:sha256:other"}
    r = post_zip(client, data, filename="z.zip", dry_run=True)
    assert r.status_code == 409 and "different source" in r.json()["detail"]


@pytest.mark.parametrize("data,status", [
    (b"definitely not a zip", 400),
    (b"", 400),
])
def test_zip_bad_uploads(env, client, data, status):
    r = post_zip(client, data, filename="bad.zip", dry_run=True)
    assert r.status_code == status and leftovers(env) == []


def test_zip_without_python_and_traversal(env, client, tmp_path):
    r = post_zip(client, build_zip(tmp_path / "d.zip", {"README.md": "x"}).read_bytes(),
                 filename="d.zip", dry_run=True)
    assert r.status_code == 400 and "no supported source files" in r.json()["detail"]
    r = post_zip(client, build_zip(tmp_path / "t.zip", {"../x.py": "x"}).read_bytes(),
                 filename="t.zip", dry_run=True)
    assert r.status_code == 400 and "Unsafe" in r.json()["detail"]
    assert leftovers(env) == []


def test_zip_upload_size_limit(env, client, monkeypatch, tmp_path):
    small = env.settings.model_copy(update={"max_archive_bytes": 100})
    monkeypatch.setattr(main, "get_settings", lambda: small)
    r = post_zip(client, fixture_zip(tmp_path / "z.zip").read_bytes(), filename="z.zip",
                 dry_run=True)
    assert r.status_code == 413 and leftovers(env) == []


def test_zip_filename_is_sanitised(env, client, tmp_path):
    rep = post_zip(client, fixture_zip(tmp_path / "z.zip").read_bytes(),
                   filename="../../etc/<evil>.zip", dry_run=True).json()
    assert rep["root_path"].startswith("zip:_evil_.zip") and rep["project_name"] == "_evil_"


# ------------------------------------------------------------------ Streamlit UI (in-process)

@pytest.fixture
def ui(env, client, monkeypatch):
    """Run ui/app.py with streamlit's AppTest; its httpx calls go to the in-process API."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(main, "queries", type("Q", (), {
        "list_projects": staticmethod(lambda: [
            {"id": p["id"], "name": p.get("name"), "sync_status": p.get("sync_status"),
             "source_kind": p.get("source_kind")} for p in env.projects.values()]),
        "QueryInputError": main.queries.QueryInputError}))
    monkeypatch.setattr(main.db, "ping", lambda: {"ok": True})

    empty_stats = {"nodes": {}, "relationships": {}, "call_confidence": [],
                   "import_resolution": [], "top_imported_files": [], "top_called_functions": []}

    def request(method, url, timeout=None, **kw):
        path = url.replace(main.get_settings().codeatlas_api_url, "")
        # graph-browsing reads are out of scope here: canned empty answers, no Neo4j
        if path.endswith("/architecture"):
            return httpx.Response(200, json=EMPTY_ARCHITECTURE)
        if path.startswith("/graph/"):
            return httpx.Response(200, json={"nodes": [], "edges": [], "cypher": []})
        if path.endswith("/stats"):
            return httpx.Response(200, json=empty_stats)
        if path.endswith(("/files", "/functions")):
            return httpx.Response(200, json=[])
        return client.request(method, path, **kw)
    monkeypatch.setattr(httpx, "request", request)
    return AppTest.from_file(str(FIXTURE.parents[2] / "ui" / "app.py"), default_timeout=60)


def test_ui_local_dry_run_then_confirmed_write(env, ui):
    at = ui.run()
    assert not at.exception
    assert at.radio(key="src_kind").options == ["Local Directory", "GitHub URL", "ZIP Upload"]
    at.text_input(key="src_path").input(str(FIXTURE)).run()
    dry = next(b for b in at.button if b.label.startswith("Dry Run"))
    at = dry.click().run()
    assert not at.exception and env.synced == []
    assert any("Dry run (nothing written)" in e.label for e in at.expander)
    # the dry run produced a target card + confirmation; writing without ticking it is refused
    confirm = at.checkbox[0]
    assert "create this project" in confirm.label
    write = next(b for b in at.button if b.label == "Analyze & Write to Neo4j Aura")
    at = write.click().run()
    assert env.synced == [] and any("Tick the confirmation" in w.value for w in at.warning)
    at.checkbox[0].check().run()
    write = next(b for b in at.button if b.label == "Analyze & Write to Neo4j Aura")
    at = write.click().run()
    assert not at.exception and len(env.synced) == 1
    assert any("Synced to Neo4j Aura" in e.label for e in at.expander)


def test_ui_github_write_shows_target_before_writing(env, ui):
    at = ui.run()
    at.radio(key="src_kind").set_value("GitHub URL").run()
    at.text_input(key="src_url").input("https://github.com/owner/repo").run()
    write = next(b for b in at.button if b.label == "Analyze & Write to Neo4j Aura")
    at = write.click().run()
    assert not at.exception and env.synced == []
    assert any("github.com/Owner/Repo@main" in m.value for m in at.markdown)
    at.checkbox[0].check().run()
    at = next(b for b in at.button if b.label == "Analyze & Write to Neo4j Aura").click().run()
    assert not at.exception and len(env.synced) == 1
