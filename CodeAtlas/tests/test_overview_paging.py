"""Complete-graph paging for the overview (offline).

`FakeGraphDB` evaluates the overview page contract documented in queries.overview_query
(stable id order, `after` cursor, `$fetch` look-ahead, relationships to nodes on this or
earlier pages, project + label + type filters) over an in-memory graph made of real
neo4j.graph Node/Relationship objects, so the payload builder, cursor handling, totals and
UI run unmodified. The Cypher text itself is checked statically; executing it on Aura is a
separate, approval-gated step.
"""

import re

import httpx
import pytest
from fastapi.testclient import TestClient
from neo4j.graph import Graph, Node

from codeatlas.cli import catalog
from codeatlas.graph import queries
from codeatlas.graph.sync import REL_VARIANTS

from .conftest import EMPTY_ARCHITECTURE, FIXTURE

PID, OTHER = "big-0001", "other-0002"


class FakeGraphDB:
    def __init__(self):
        self.g = Graph()
        self.nodes: dict[str, Node] = {}
        self.rels: list = []
        self.n = 0

    def node(self, label, nid, pid, **props):
        self.n += 1
        node = Node(self.g, f"n{self.n}", self.n, [label],
                    {"id": nid, "project_id": pid, "name": nid.rsplit(":", 1)[-1], **props})
        self.nodes[nid] = node
        return node

    def rel(self, rtype, a, b):
        self.n += 1
        r = self.g.relationship_type(rtype)(self.g, f"r{self.n}", self.n, {})
        r._start_node, r._end_node = self.nodes[a], self.nodes[b]
        self.rels.append(r)
        return r

    # -- query evaluation -------------------------------------------------------
    def read(self, query, params=None):
        params = params or {}
        if "$fetch" in query:
            return self._page(params)
        if query == queries.NODE_STATS:
            counts = {}
            for n in self.nodes.values():
                if n["project_id"] == params["pid"]:
                    counts[next(iter(n.labels))] = counts.get(next(iter(n.labels)), 0) + 1
            return [{"label": k, "count": counts.get(k, 0)} for k in queries.LABELS]
        if query == queries._rel_variant_counts_query():
            out = []
            for key, (src, rtype, dst) in REL_VARIANTS.items():
                out.append({"variant": key, "count": sum(
                    1 for r in self.rels if r.type == rtype and src in r.start_node.labels
                    and dst in r.end_node.labels and r.start_node["project_id"] == params["pid"]
                    and r.end_node["project_id"] == params["pid"])})
            return out
        raise AssertionError(f"unexpected query: {query[:80]}")

    def _page(self, p):
        cand = sorted((n for n in self.nodes.values()
                       if next(iter(n.labels)) in p["labels"] and n["project_id"] == p["pid"]
                       and n["id"] > p["after"]), key=lambda n: n["id"])
        ns = cand[:p["fetch"]]
        page, more = ns[:p["page_size"]], len(ns) > p["page_size"]
        if not page:
            return []
        upto, ids = page[-1]["id"], {n["id"] for n in page}
        rels, neighbours = {}, {}
        for r in self.rels:
            for a, b in ((r.start_node, r.end_node), (r.end_node, r.start_node)):
                if (a["id"] in ids and b["project_id"] == p["pid"] and b["id"] <= upto
                        and r.type in p["rel_types"]
                        and next(iter(b.labels)) in p["labels"]):
                    rels[r.element_id], neighbours[b.element_id] = r, b
        return [{"nodes": page, "rels": list(rels.values()),
                 "neighbours": list(neighbours.values()), "more": more, "upto": upto}]


@pytest.fixture
def fake(monkeypatch):
    db = FakeGraphDB()
    db.node("Project", PID, PID)
    for i in range(40):
        db.node("File", f"{PID}:file:pkg/f{i:02}.py", PID, path=f"pkg/f{i:02}.py")
        db.rel("CONTAINS", PID, f"{PID}:file:pkg/f{i:02}.py")
    for m in ("os", "json", "re", "typing", "requests"):
        db.node("Module", f"{PID}:module:{m}", PID)
    for i in range(20):
        cid = f"{PID}:class:pkg/f{i:02}.py:C{i}"
        db.node("Class", cid, PID)
        db.rel("DEFINES", f"{PID}:file:pkg/f{i:02}.py", cid)
        if i:
            db.rel("INHERITS", cid, f"{PID}:class:pkg/f00.py:C0")
    for i in range(120):
        fid = f"{PID}:function:pkg/f{i % 40:02}.py:fn{i:03}"
        db.node("Function", fid, PID)
        if i < 20:
            db.rel("HAS_METHOD", f"{PID}:class:pkg/f{i:02}.py:C{i}", fid)
        else:
            db.rel("DEFINES", f"{PID}:file:pkg/f{i % 40:02}.py", fid)
        if i:
            db.rel("CALLS", fid, f"{PID}:function:pkg/f00.py:fn000")
    for i in range(3):  # isolated: no relationships at all
        db.node("Function", f"{PID}:function:pkg/zz.py:orphan{i}", PID)
    for i in range(39):
        db.rel("IMPORTS", f"{PID}:file:pkg/f{i + 1:02}.py", f"{PID}:file:pkg/f{i:02}.py")
    for i, m in enumerate(("os", "json", "re", "typing", "requests")):
        db.rel("IMPORTS", f"{PID}:file:pkg/f{i:02}.py", f"{PID}:module:{m}")
    # another project, plus a (never legitimately created) cross-project edge
    db.node("Project", OTHER, OTHER)
    db.node("File", f"{OTHER}:file:x.py", OTHER)
    db.rel("CONTAINS", OTHER, f"{OTHER}:file:x.py")
    db.rel("IMPORTS", f"{PID}:file:pkg/f05.py", f"{OTHER}:file:x.py")
    monkeypatch.setattr(queries.db, "read", db.read)
    return db


def project_nodes(db, labels=queries.LABELS):
    return {n["id"] for n in db.nodes.values()
            if n["project_id"] == PID and next(iter(n.labels)) in labels}


def project_edges(db, labels=queries.LABELS, types=queries.REL_TYPES):
    return {f"{r.start_node['id']}|{r.type}|{r.end_node['id']}" for r in db.rels
            if r.start_node["project_id"] == PID and r.end_node["project_id"] == PID
            and r.type in types and next(iter(r.start_node.labels)) in labels
            and next(iter(r.end_node.labels)) in labels}


def load_all(labels=None, rel_types=None, page_size=50):
    pages, after = [], ""
    while True:
        page = queries.overview(PID, labels, rel_types, limit=page_size, after=after)
        pages.append(page)
        if page.next_cursor is None:
            return pages
        after = page.next_cursor


def test_fixture_is_large_and_covers_everything(fake):
    assert len(project_nodes(fake)) > 150
    assert {next(iter(n.labels)) for n in fake.nodes.values()} == set(queries.LABELS)
    assert {r.type for r in fake.rels} == set(queries.REL_TYPES)


def test_paging_returns_the_complete_graph_exactly_once(fake):
    pages = load_all(page_size=50)
    assert len(pages) == 4 and [p.complete for p in pages] == [False] * 3 + [True]
    seen_nodes, edge_counts = set(), {}
    for p in pages:
        for e in p.edges:
            edge_counts[e.id] = edge_counts.get(e.id, 0) + 1
        seen_nodes |= {n.id for n in p.nodes}
    assert seen_nodes == project_nodes(fake)  # incl. Module and the isolated nodes
    assert set(edge_counts) == project_edges(fake)
    assert set(edge_counts.values()) == {1}  # no relationship delivered twice
    assert sum(p.page_nodes for p in pages) == len(project_nodes(fake))
    assert all(not n.id.startswith(OTHER) for p in pages for n in p.nodes)
    assert not any(OTHER in e.id for p in pages for e in p.edges)


def test_single_page_when_page_is_large_enough(fake):
    page = queries.overview(PID, limit=1000)
    assert page.complete and page.next_cursor is None and not page.truncated
    assert {n.id for n in page.nodes} == project_nodes(fake)
    assert {e.id for e in page.edges} == project_edges(fake)


def test_totals_and_hidden_relationships(fake):
    first = queries.overview(PID, limit=50)
    t = first.totals
    assert t["nodes"] == len(project_nodes(fake)) == t["selected_nodes"]
    assert t["relationships"] == len(project_edges(fake)) == t["selected_relationships"]
    assert t["hidden_relationships"] == 0 and t["nodes_by_label"]["Module"] == 5
    assert queries.overview(PID, limit=50, after=first.next_cursor).totals is None

    no_modules = [lbl for lbl in queries.LABELS if lbl != "Module"]
    t2 = queries.overview(PID, no_modules, limit=50).totals
    assert t2["selected_nodes"] == len(project_nodes(fake)) - 5
    assert t2["hidden_relationships"] == 5  # File-IMPORTS->Module edges, counted not dropped
    assert t2["selected_relationships"] == len(project_edges(fake, no_modules))


def test_label_and_type_filters_are_consistent(fake):
    no_modules = [lbl for lbl in queries.LABELS if lbl != "Module"]
    pages = load_all(no_modules, ["IMPORTS", "CALLS"], page_size=40)
    nodes = {n.id for p in pages for n in p.nodes}
    edges = {e.id for p in pages for e in p.edges}
    assert nodes == project_nodes(fake, no_modules)
    assert edges == project_edges(fake, no_modules, ["IMPORTS", "CALLS"])
    assert {e.split("|")[1] for e in edges} == {"IMPORTS", "CALLS"}


def test_page_size_is_clamped_and_inputs_validated(fake):
    assert queries.overview(PID, limit=10 ** 6).page_nodes == len(project_nodes(fake))
    with pytest.raises(queries.QueryInputError):
        queries.overview(PID, ["Person"])
    with pytest.raises(queries.QueryInputError):
        queries.overview(PID, rel_types=["KNOWS"])


def test_overview_cypher_shape():
    q = queries.overview_query(list(queries.LABELS))
    assert q.lstrip().startswith("CALL () {")
    assert "LIMIT $fetch" in q and "n.id > $after" in q and "b.id <= upto" in q
    assert "b.project_id = $pid" in q and "type(r) IN $rel_types" in q
    for label in queries.LABELS:
        anchor = "{id: $pid}" if label == "Project" else "{project_id: $pid}"
        assert f"MATCH (n:{label} {anchor}) WHERE n.id > $after" in q


def test_no_deprecated_call_subquery_syntax():
    for name, text in catalog().items():
        assert not re.search(r"\bCALL\s*\{", text), name
    assert "CALL () {" in queries.FIND_ENTITIES


# ------------------------------------------------------------------ through the API and UI

@pytest.fixture
def api(fake):
    from codeatlas.api import main
    return TestClient(main.app)


def test_api_paging_round_trip(api, fake):
    after, nodes, edges = "", set(), set()
    while True:
        r = api.get("/graph/overview", params={"project_id": PID, "limit": 60, "after": after})
        assert r.status_code == 200
        body = r.json()
        nodes |= {n["id"] for n in body["nodes"]}
        edges |= {e["id"] for e in body["edges"]}
        if not body["next_cursor"]:
            assert body["complete"] is True
            break
        after = body["next_cursor"]
    assert nodes == project_nodes(fake) and edges == project_edges(fake)


def test_api_defaults_include_every_label_and_type(api, fake):
    body = api.get("/graph/overview", params={"project_id": PID}).json()
    assert body["params"]["labels"] == list(queries.LABELS)
    assert body["params"]["rel_types"] == list(queries.REL_TYPES)
    assert body["params"]["page_size"] == queries.OVERVIEW_PAGE_SIZE


@pytest.fixture
def ui(api, fake, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from codeatlas.api import main
    monkeypatch.setattr(main.queries, "list_projects", lambda: [
        {"id": PID, "name": "big", "sync_status": "complete", "source_kind": "local"}])
    monkeypatch.setattr(main.db, "ping", lambda: {"ok": True})
    monkeypatch.setenv("CODEATLAS_AUTO_LOAD_NODES", "100")

    def request(method, url, timeout=None, **kw):
        path = url.replace(main.get_settings().codeatlas_api_url, "")
        if path.endswith("/architecture"):
            return httpx.Response(200, json=EMPTY_ARCHITECTURE)
        if path.endswith("/stats"):
            return httpx.Response(200, json={
                "nodes": {}, "relationships": {}, "call_confidence": [], "import_resolution": [],
                "top_imported_files": [], "top_called_functions": []})
        if path.endswith(("/files", "/functions")):
            return httpx.Response(200, json=[])
        return api.request(method, path, **kw)
    monkeypatch.setattr(httpx, "request", request)
    return AppTest.from_file(str(FIXTURE.parents[2] / "ui" / "app.py"), default_timeout=60)


def test_ui_partial_then_complete(ui, fake):
    at = ui.run()
    assert not at.exception
    assert next(m for m in at.multiselect if m.label == "Node types").value == list(queries.LABELS)  # Module included by default
    next(s for s in at.slider if s.label == "Nodes per page").set_value(100).run()
    warn = [w.value for w in at.warning]
    assert any("Partial view" in w and "Displayed **100** nodes" in w for w in warn), warn
    at = next(b for b in at.button if b.label == "Load all remaining").click().run()
    total_n, total_r = len(project_nodes(fake)), len(project_edges(fake))
    ok = [s.value for s in at.success]
    assert any("Complete graph" in s and f"**{total_n}** nodes / {total_n}" in s
               and f"**{total_r}** relationships / {total_r}" in s for s in ok), ok
    assert not any("Partial view" in w.value for w in at.warning)


def test_ui_reports_hidden_relationships(ui, fake):
    at = ui.run()
    next(m for m in at.multiselect if m.label == "Node types").set_value([lbl for lbl in queries.LABELS if lbl != "Module"]).run()
    assert any("5 relationships are hidden" in c.value for c in at.caption)
