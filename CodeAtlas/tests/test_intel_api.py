"""Phase 2 API, snapshot loading and Streamlit integration (offline).

Snapshots come from the real analyzer on local fixture repos; Neo4j and the LLM are faked.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from codeatlas.api import main
from codeatlas.graph import db, queries
from codeatlas.intel import service, snapshot as snap_mod
from codeatlas.llm.client import LLMError

from .conftest import FIXTURE, LAYERED_REPO, snapshot_of, write_repo


@pytest.fixture
def env(tmp_path, offline_settings, monkeypatch):
    root = write_repo(tmp_path / "shopapp", LAYERED_REPO)
    snaps = {"shop-1": snapshot_of(root, offline_settings),
             "sample-2": snapshot_of(FIXTURE, offline_settings)}
    roots = {"shop-1": str(root), "sample-2": str(FIXTURE)}

    def load(pid):
        if pid not in snaps:
            raise snap_mod.ProjectNotFound(f"Unknown project {pid}")
        return snaps[pid]
    monkeypatch.setattr(service, "load_snapshot", load)
    monkeypatch.setattr(service.queries, "project", lambda pid: {
        "id": pid, "name": pid, "root_path": roots.get(pid), "source_kind": "local"}
        if pid in snaps else None)

    def no_db(*a, **k):
        raise AssertionError("offline test touched Neo4j")
    monkeypatch.setattr(db, "read", no_db)
    monkeypatch.setattr(db, "write", no_db)
    calls = []

    def fake_chat(system, user, max_tokens=700, settings=None):
        calls.append(user)
        if env_state["llm_fails"]:
            raise LLMError("rate_limited", "LLM provider returned HTTP 429.", provider_status=429)
        return "**What it does**: grounded text."
    env_state = {"llm_fails": False, "calls": calls, "snaps": snaps}
    monkeypatch.setattr(service.llm, "chat", fake_chat)
    return env_state


@pytest.fixture
def client(env):
    return TestClient(main.app)


def nid(env, pid, qualname):
    return next(n["id"] for n in env["snaps"][pid].nodes.values()
                if n.get("qualname") == qualname)


def test_architecture_endpoint(client):
    r = client.get("/projects/shop-1/architecture")
    assert r.status_code == 200
    body = r.json()
    assert {c["name"]: c["layer"] for c in body["components"]}["app/api/routes"] == "api"
    assert body["entry_points"] == ["app/api/routes.py"]
    assert client.get("/projects/nope/architecture").status_code == 404


@pytest.mark.parametrize("kind,target", [
    ("overview", None), ("entry", None), ("component", "app/services/orders"),
    ("dependency", "app/services/orders"), ("impact", "app/models/order"),
])
def test_tour_endpoint(client, kind, target):
    params = {"kind": kind, **({"target": target} if target else {})}
    r = client.get("/projects/shop-1/tours", params=params)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind and r.json()["steps"]


def test_execution_tour_and_errors(client, env):
    r = client.get("/projects/shop-1/tours",
                   params={"kind": "execution", "target": nid(env, "shop-1", "place_order")})
    assert r.status_code == 200 and r.json()["steps"][0]["title"] == "place_order"
    assert client.get("/projects/shop-1/tours", params={"kind": "magic"}).status_code == 400
    assert client.get("/projects/shop-1/tours", params={"kind": "component"}).status_code == 400
    assert client.get("/projects/nope/tours").status_code == 404


def test_tour_narration_success_and_graceful_failure(client, env):
    r = client.get("/projects/shop-1/tours", params={"kind": "entry", "narrate": True})
    body = r.json()
    assert r.status_code == 200 and body["narrative"].startswith("**What it does**")
    assert "create_order" in env["calls"][-1]  # the LLM saw the deterministic steps
    env["llm_fails"] = True
    r = client.get("/projects/shop-1/tours", params={"kind": "entry", "narrate": True})
    body = r.json()
    assert r.status_code == 200 and body["narrative"] is None
    assert body["llm_error"]["code"] == "rate_limited" and body["steps"]  # tour still useful


def test_explain_facts_only_llm_and_fallback(client, env):
    node = nid(env, "shop-1", "place_order")
    r = client.get("/projects/shop-1/explain", params={"node_id": node})
    body = r.json()
    assert r.status_code == 200 and body["interpretation"] is None and not body["llm_used"]
    assert body["facts"]["source_excerpt"]["verified"] is True
    assert env["calls"] == []  # no LLM call unless asked
    r = client.get("/projects/shop-1/explain", params={"node_id": node, "use_llm": True})
    body = r.json()
    assert body["llm_used"] and body["interpretation"].startswith("**What it does**")
    assert "def place_order" in env["calls"][-1]  # verified excerpt sent, facts first
    env["llm_fails"] = True
    r = client.get("/projects/shop-1/explain", params={"node_id": node, "use_llm": True})
    body = r.json()
    assert r.status_code == 200 and body["interpretation"] is None
    assert body["llm_error"]["code"] == "rate_limited" and body["facts"]["calls"]
    assert client.get("/projects/shop-1/explain", params={"node_id": "nope"}).status_code == 404


def test_explain_has_no_source_for_remote_sources(client, env, monkeypatch):
    monkeypatch.setattr(service.queries, "project", lambda pid: {
        "id": pid, "root_path": "github.com/o/shop@main", "source_kind": "github"})
    body = client.get("/projects/shop-1/explain",
                      params={"node_id": nid(env, "shop-1", "place_order")}).json()
    assert body["facts"]["source_excerpt"] is None


def test_search_endpoint_scoping_and_honesty(client, env):
    r = client.get("/projects/shop-1/search", params={"q": "where is the order validated"})
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "lexical+graph" and body["semantic"] is False
    assert body["results"][0]["name"] == "validate_order"
    assert all(x["node_id"] in env["snaps"]["shop-1"].nodes for x in body["results"])
    other = client.get("/projects/sample-2/search", params={"q": "order", "limit": 100}).json()
    assert not {x["node_id"] for x in other["results"]} & set(env["snaps"]["shop-1"].nodes)
    filt = client.get("/projects/shop-1/search",
                      params={"q": "order", "labels": ["Class"]}).json()
    assert filt["results"] and {x["label"] for x in filt["results"]} == {"Class"}
    assert client.get("/projects/shop-1/search").status_code == 422
    assert client.get("/projects/nope/search", params={"q": "x"}).status_code == 404


def test_existing_endpoints_unchanged(client):
    assert client.post("/ask", json={"project_id": "nope", "question": "What does it do?"}) \
        .status_code in (400, 503)  # existing Q&A path still routed (fails on fake DB)


# ------------------------------------------------------------------ snapshot loader

def test_load_snapshot_scoped_cached_and_drops_foreign_edges(monkeypatch):
    monkeypatch.setattr(snap_mod, "_cache", {})
    proj = {"id": "p1", "name": "one", "last_run_id": "r1"}
    monkeypatch.setattr(snap_mod.queries, "project", lambda pid: proj if pid == "p1" else None)
    seen = []

    def read(q, params=None):
        seen.append((q, params))
        assert params == {"pid": "p1"}
        if q == queries.SNAPSHOT_NODES:
            return [{"label": "File", "props": {"id": "p1:file:a.py", "path": "a.py",
                                                "project_id": "p1"}},
                    {"label": "Function", "props": {"id": "p1:function:a.py:f", "name": "f"}}]
        return [{"s": "p1:file:a.py", "t": "DEFINES", "d": "p1:function:a.py:f",
                 "props": {"last_run_id": "r1"}},
                {"s": "p1:file:a.py", "t": "IMPORTS", "d": "p2:file:x.py", "props": {}}]
    monkeypatch.setattr(snap_mod.db, "read", read)
    s = snap_mod.load_snapshot("p1")
    assert set(s.nodes) == {"p1:file:a.py", "p1:function:a.py:f"}
    assert [(e.type, e.target) for e in s.edges] == [("DEFINES", "p1:function:a.py:f")]
    assert s.edges[0].props == {}
    assert len(seen) == 2 and snap_mod.load_snapshot("p1") is s and len(seen) == 2  # cached
    proj["last_run_id"] = "r2"  # a new sync invalidates the cache
    assert snap_mod.load_snapshot("p1") is not s and len(seen) == 4
    with pytest.raises(snap_mod.ProjectNotFound):
        snap_mod.load_snapshot("p2")


def test_snapshot_queries_are_project_scoped():
    for q in (queries.SNAPSHOT_NODES, queries.snapshot_rels_query()):
        assert "$pid" in q and "DELETE" not in q and "MERGE" not in q and "SET " not in q
    assert queries.snapshot_rels_query().count("{project_id: $pid}") == 2 * 7


# ------------------------------------------------------------------ Streamlit integration

@pytest.fixture
def ui(env, client, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(main.queries, "list_projects", lambda: [
        {"id": "shop-1", "name": "shop", "sync_status": "complete", "source_kind": "local"}])
    monkeypatch.setattr(main.db, "ping", lambda: {"ok": True})
    snap = env["snaps"]["shop-1"]

    def request(method, url, timeout=None, **kw):
        path = url.replace(main.get_settings().codeatlas_api_url, "")
        if path.startswith("/graph/"):
            return httpx.Response(200, json={"nodes": [], "edges": [], "cypher": [],
                                             "complete": True, "next_cursor": None})
        if path.endswith("/stats"):
            return httpx.Response(200, json={
                "nodes": {}, "relationships": {}, "call_confidence": [], "import_resolution": [],
                "top_imported_files": [], "top_called_functions": []})
        if path.endswith("/files"):
            return httpx.Response(200, json=[{"id": f["id"], "path": f["path"]}
                                             for f in snap.of_label("File")])
        if path.endswith("/functions"):
            return httpx.Response(200, json=[{"id": f["id"], "qualname": f["qualname"],
                                              "file": f["file_path"]}
                                             for f in snap.of_label("Function")])
        return client.request(method, path, **kw)
    monkeypatch.setattr(httpx, "request", request)
    return AppTest.from_file(str(FIXTURE.parents[2] / "ui" / "app.py"), default_timeout=60)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_ui_architecture_tour_explain_search_flow(ui, env):
    at = ui.run()
    assert not at.exception
    # 1-2. architecture of the selected project
    assert any("API & entry points" == m.label for m in at.metric)
    comp_box = next(s for s in at.selectbox if s.label == "Component")
    at = comp_box.set_value("app/services/orders").run()
    assert any("used by the api layer" in w.value for w in at.markdown)
    # 3-4. component tour, navigate steps
    at = _button(at, "Component tour").click().run()
    assert not at.exception
    assert any("Component tour: app/services/orders" in m.value for m in at.markdown)
    assert any(p.value and "Step 1 of" in str(p.proto.text) for p in at.get("progress"))
    at = _button(at, "Next ▶").click().run()
    assert any("Step 2 of" in str(p.proto.text) for p in at.get("progress"))
    # 5-6. explain a symbol from the tour
    explain_btn = next(b for b in at.button if b.label.startswith("Explain "))
    at = explain_btn.click().run()
    assert any("Observed facts (from the graph)" in m.value for m in at.markdown)
    assert any("Unknowns" in m.value for m in at.markdown)
    assert env["calls"] == []  # no LLM unless asked
    # 7-8. search, open a result's explanation
    at.text_input(key="search_q").input("where is the order saved to the database").run()
    assert any("lexical + graph search" in c.value.lower() for c in at.caption)
    at = next(b for b in at.button if b.key and b.key.startswith("search-x-")).click().run()
    assert not at.exception
    assert any(m.value.startswith("### Function `save_order`") for m in at.markdown)


def test_ui_llm_interpretation_failure_is_shown(ui, env):
    env["llm_fails"] = True
    at = ui.run()
    at.text_input(key="explain_q").input("place_order").run()
    at = _button(at, "Explain").click().run()
    at = _button(at, "Generate LLM interpretation").click().run()
    assert not at.exception
    assert any("No LLM interpretation" in w.value for w in at.warning)
    assert any("Observed facts (from the graph)" in m.value for m in at.markdown)
