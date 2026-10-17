"""Phase 2 services on real analysis output (offline): architecture, tours, explanations,
search. Repositories are analyzed with the production pipeline; no Neo4j, no LLM."""

import pytest

from codeatlas.intel import architecture as arch_mod
from codeatlas.intel.architecture import UNCLASSIFIED, Counter as _C, _decide, analyze_architecture
from codeatlas.intel.explain import SymbolNotFound, facts_prompt, symbol_facts
from codeatlas.intel.search import SEARCH_MODE, query_terms, search
from codeatlas.intel.snapshot import Edge, Snapshot
from codeatlas.intel.tours import MAX_STEPS, TOUR_KINDS, TourError, build_tour

from .conftest import FIXTURE, LAYERED_REPO, snapshot_of, write_repo


@pytest.fixture
def layered(tmp_path, offline_settings):
    root = write_repo(tmp_path / "shopapp", LAYERED_REPO)
    snap = snapshot_of(root, offline_settings)
    return root, snap, analyze_architecture(snap)


def comp(report, name):
    return next(c for c in report.components if c.name == name)


def fid(snap, qualname):
    return next(n["id"] for n in snap.nodes.values() if n.get("qualname") == qualname)


# ------------------------------------------------------------------ architecture

def test_recognisable_layers_with_evidence(layered):
    _, _, rep = layered
    expected = {"app/api/routes": "api", "app/services/orders": "business",
                "app/models/order": "domain", "app/db/repo": "data",
                "tests/test_orders": "tests", "app/utils/fmt": "utilities"}
    assert {c.name: c.layer for c in rep.components if c.name in expected} == expected
    routes = comp(rep, "app/api/routes")
    assert routes.confidence == "high"
    assert any("fastapi" in e for e in routes.evidence)
    assert any("route decorator" in e for e in routes.evidence)
    assert any("used by the api layer" in e for e in comp(rep, "app/services/orders").evidence)
    # the project's own package name is not evidence
    assert not any("'app'" in e for c in rep.components for e in c.evidence)


def test_ambiguous_or_unevidenced_components_are_not_forced(layered):
    _, _, rep = layered
    thing = comp(rep, "app/stuff/thing")
    assert thing.layer == UNCLASSIFIED and thing.confidence == "none"
    assert any("left unclassified" in n for n in rep.notes)
    layer, conf, between = _decide(_C({"data": 3.0, "domain": 2.5}))
    assert (layer, conf) == (UNCLASSIFIED, "low") and set(between) == {"data", "domain"}


def test_dependencies_cycles_violations_and_entry_points(layered):
    _, _, rep = layered
    orders = comp(rep, "app/services/orders")
    assert {d.component for d in orders.depends_on} == {"app/db/repo", "app/models/order"}
    assert {d.component for d in orders.used_by} >= {"app/api/routes", "app/db/repo"}
    assert ["app/db/repo", "app/services/orders"] in rep.component_cycles
    assert ["app/db/repo.py", "app/services/orders.py"] in rep.file_cycles
    v = [(x.source_component, x.target_component) for x in rep.violations]
    assert v == [("app/db/repo", "app/services/orders")]  # data -> business
    assert "app/db/repo.py imports app/services/orders.py" in rep.violations[0].example
    assert rep.entry_points == ["app/api/routes.py"]  # route handler nobody imports
    edge = next(e for e in rep.layer_edges if (e.source, e.target) == ("data", "business"))
    assert edge.possible_violation


def test_directory_components_for_larger_projects(layered, monkeypatch):
    _, snap, _ = layered
    monkeypatch.setattr(arch_mod, "FILE_COMPONENT_LIMIT", 0)
    rep = analyze_architecture(snap)
    names = {c.name for c in rep.components}
    assert {"app/api", "app/services", "app/db", "app/models", "tests"} <= names
    assert ["app/db", "app/services"] in rep.component_cycles
    assert not any("__init__" in f.path for f in rep.files)  # empty package markers omitted


def test_missing_metadata_and_isolated_nodes():
    nodes = {
        "p:file:a.py": {"id": "p:file:a.py", "label": "File", "path": "a.py"},
        "p:function:a.py:f": {"id": "p:function:a.py:f", "label": "Function", "name": "f"},
        "p:module:os": {"id": "p:module:os", "label": "Module", "name": "os"},
    }
    snap = Snapshot("p", "p", nodes, [Edge("p:file:a.py", "DEFINES", "p:function:a.py:f")])
    rep = analyze_architecture(snap)
    assert [c.name for c in rep.components] == ["a"] and rep.components[0].layer == UNCLASSIFIED
    assert any("No entry point" in n for n in rep.notes)
    f = symbol_facts(snap, rep, "p:function:a.py:f")
    assert f.location is None and any("No source location" in u for u in f.unknowns)
    assert search(snap, "f", rep).total >= 1


def test_architecture_is_deterministic(layered, offline_settings):
    root, snap, rep = layered
    again = analyze_architecture(snapshot_of(root, offline_settings))
    assert again.model_dump() == rep.model_dump()


# ------------------------------------------------------------------ tours

def test_overview_tour_follows_layer_order(layered):
    _, snap, rep = layered
    t = build_tour(snap, rep, "overview")
    layers = [s.layer for s in t.steps if s.kind == "layer"]
    assert layers == [lyr.layer for lyr in rep.layers]
    assert layers.index("api") < layers.index("business") < layers.index("data")
    titles = [s.title for s in t.steps]
    assert "Entry points" in titles and "Tightly coupled components" in titles
    assert "Possible layering violations" in titles
    assert all(not s.observed for s in t.steps if s.kind == "layer")  # layers are inferred


def test_entry_tour_follows_resolved_calls_in_order(layered):
    _, snap, rep = layered
    t = build_tour(snap, rep, "entry")
    assert [s.title for s in t.steps] == ["app/api/routes.py", "create_order", "place_order",
                                          "save_order", "validate_order"]
    assert all(s.observed for s in t.steps)
    place = t.steps[2]
    assert place.location == {"file_path": "app/services/orders.py", "start_line": 6,
                              "end_line": 11}
    assert "Called by create_order at line(s) 12" in place.explanation
    assert place.layer == "business" and place.component == "app/services/orders"
    assert any("could not resolve" in n for n in t.notes)
    assert build_tour(snap, rep, "entry").model_dump() == t.model_dump()  # deterministic


def test_missing_entry_point(tmp_path, offline_settings):
    snap = snapshot_of(write_repo(tmp_path / "lib", {"helpers.py": "def h():\n    return 1\n"}),
                       offline_settings)
    t = build_tour(snap, analyze_architecture(snap), "entry")
    assert t.steps == [] and "No entry point" in t.notes[0]


def test_incomplete_call_graph_falls_back_to_marked_inferred_imports(tmp_path, offline_settings):
    repo = {"cli.py": "import worker\n\n\ndef main():\n    getattr(worker, 'go')()\n",
            "worker.py": "def go():\n    return 1\n"}
    snap = snapshot_of(write_repo(tmp_path / "dyn", repo), offline_settings)
    t = build_tour(snap, analyze_architecture(snap), "entry")
    assert [s.title for s in t.steps] == ["cli.py", "main", "worker.py"]
    assert t.steps[-1].observed is False
    assert any("cannot be traced" in n for n in t.notes)


def test_execution_tour_handles_cycles_and_truncation():
    nodes = {f"p:function:m.py:f{i}": {"id": f"p:function:m.py:f{i}", "label": "Function",
                                       "name": f"f{i}", "qualname": f"f{i}", "file_path": "m.py",
                                       "start_line": i + 1, "end_line": i + 1}
             for i in range(40)}
    nodes["p:file:m.py"] = {"id": "p:file:m.py", "label": "File", "path": "m.py"}
    edges = [Edge("p:file:m.py", "DEFINES", n) for n in nodes if ":function:" in n]
    edges += [Edge("p:function:m.py:f0", "CALLS", "p:function:m.py:f1", {"lines": [1]}),
              Edge("p:function:m.py:f1", "CALLS", "p:function:m.py:f0", {"lines": [2]})]
    edges += [Edge("p:function:m.py:f1", "CALLS", f"p:function:m.py:f{i}", {"lines": [3]})
              for i in range(2, 40)]
    snap = Snapshot("p", "p", nodes, edges)
    t = build_tour(snap, analyze_architecture(snap), "execution", "p:function:m.py:f0")
    assert t.steps[0].title == "f0" and t.steps[1].title == "f1"
    assert t.steps[2].title == "↺ f0"  # cycle back is shown, not expanded
    assert len(t.steps) == MAX_STEPS and t.truncated


def test_component_dependency_and_impact_tours(layered):
    _, snap, rep = layered
    c = build_tour(snap, rep, "component", "app/services/orders")
    assert c.steps[0].kind == "component" and c.steps[0].layer == "business"
    assert any(s.title == "validate_order" and "2 known caller" in s.explanation
               for s in c.steps)
    d = build_tour(snap, rep, "dependency", "app/services/orders")
    dep = next(s for s in d.steps if s.title == "→ app/db/repo")
    assert any("app/services/orders.py imports app/db/repo.py (line 2" in e for e in dep.evidence)
    i = build_tour(snap, rep, "impact", fid(snap, "validate_order"))
    affected = [s.title for s in i.steps]
    assert {"app/services/orders.py::place_order", "app/db/repo.py::save_order"} <= set(affected)
    assert "app/api/routes.py::create_order" in affected  # two steps away
    assert i.steps[-1].title == "Affected components"


def test_tours_contain_only_the_projects_own_data(layered, offline_settings):
    _, snap, rep = layered
    other = snapshot_of(FIXTURE, offline_settings)
    other_rep = analyze_architecture(other)
    for s, r, foreign in ((snap, rep, other), (other, other_rep, snap)):
        files = {n.get("path") for n in s.nodes.values() if n["label"] == "File"}
        for kind in ("overview", "entry"):
            for step in build_tour(s, r, kind).steps:
                assert step.node_id is None or step.node_id in s.nodes
                assert step.node_id not in foreign.nodes
                assert not step.location or step.location["file_path"] in files


def test_tour_errors(layered):
    _, snap, rep = layered
    with pytest.raises(TourError, match="Unknown tour kind"):
        build_tour(snap, rep, "magic")
    for kind in set(TOUR_KINDS) - {"overview", "entry"}:
        with pytest.raises(TourError):
            build_tour(snap, rep, kind)
    with pytest.raises(TourError, match="Unknown component"):
        build_tour(snap, rep, "component", "nope")
    with pytest.raises(TourError, match="function"):
        build_tour(snap, rep, "execution", "app/services/orders")


# ------------------------------------------------------------------ explanations

def test_function_facts_with_verified_source(layered):
    root, snap, rep = layered
    f = symbol_facts(snap, rep, fid(snap, "place_order"), str(root))
    assert (f.kind, f.signature, f.params) == ("function", "place_order(payload)", ["payload"])
    assert f.location == {"file_path": "app/services/orders.py", "start_line": 6, "end_line": 11}
    assert {r.display for r in f.calls} == {"app/db/repo.py::save_order",
                                            "app/services/orders.py::validate_order"}
    assert [r.display for r in f.callers] == ["app/api/routes.py::create_order",
                                              "tests/test_orders.py::test_place_order"]
    assert f.callers[0].lines == [12]
    assert [r.label for r in f.belongs_to] == ["File"]
    assert f.layer == "business" and "Order" in f.unresolved_calls
    assert any("calls app/db/repo.py::save_order in the Data" in h for h in f.side_effect_hints)
    assert f.source_excerpt and f.source_excerpt.verified
    assert f.source_excerpt.text.startswith("def place_order(payload):")
    assert any("return value are not extracted" in u for u in f.unknowns)


def test_source_excerpt_refused_when_file_changed(layered):
    root, snap, rep = layered
    (root / "app/services/orders.py").write_text("# rewritten\n")
    f = symbol_facts(snap, rep, fid(snap, "place_order"), str(root))
    assert f.source_excerpt is None
    assert any("not available unchanged" in u for u in f.unknowns)
    assert symbol_facts(snap, rep, fid(snap, "place_order"), None).source_excerpt is None


def test_method_class_file_and_module_facts(layered):
    root, snap, rep = layered
    m = symbol_facts(snap, rep, fid(snap, "Order.describe"), str(root))
    assert m.kind == "method" and any(r.label == "Class" for r in m.belongs_to)
    c = symbol_facts(snap, rep, fid(snap, "Order"), str(root))
    assert c.kind == "class" and c.bases == ["BaseModel"]
    assert [r.display for r in c.methods] == ["app/models/order.py::Order.describe"]
    assert any("Base classes outside the project" in u for u in c.unknowns)
    file_id = next(n["id"] for n in snap.nodes.values() if n.get("path") == "app/db/repo.py")
    f = symbol_facts(snap, rep, file_id, str(root))
    assert f.kind == "file" and {r.display for r in f.imports} >= {"sqlite3",
                                                                  "app/services/orders.py"}
    assert {r.display for r in f.imported_by} == {"app/services/orders.py"}
    mod = symbol_facts(snap, rep, next(n["id"] for n in snap.nodes.values()
                                       if n.get("name") == "sqlite3"))
    assert mod.kind == "external module" and [r.display for r in mod.imported_by] == [
        "app/db/repo.py"]


def test_missing_symbol(layered):
    _, snap, rep = layered
    with pytest.raises(SymbolNotFound):
        symbol_facts(snap, rep, "nope")


def test_facts_prompt_contains_only_the_symbol_context(layered):
    root, snap, rep = layered
    f = symbol_facts(snap, rep, fid(snap, "validate_order"), str(root))
    prompt = facts_prompt(f)
    assert "validate_order" in prompt and "Called by" in prompt and "save_order" in prompt
    assert "money" not in prompt and "frobnicate" not in prompt  # unrelated code not sent
    assert "Unknowns:" in prompt and len(prompt) <= 9000


# ------------------------------------------------------------------ search

def test_exact_and_fuzzy_matches(layered):
    _, snap, rep = layered
    r = search(snap, "place_order", rep)
    assert r.results[0].name == "place_order" and r.results[0].matched[0] == "exact name match"
    fz = search(snap, "validte order", rep)
    top = [x.name for x in fz.results[:3]]
    assert "validate_order" in top
    hit = next(x for x in fz.results if x.name == "validate_order")
    assert any("close to 'validt'" in m for m in hit.matched)


def test_natural_language_queries(layered):
    _, snap, rep = layered
    r = search(snap, "Where is the order saved to the database?", rep)
    assert r.terms == ["ord", "sav", "databas"]  # stop words removed, light stemming
    assert "save_order" in [x.name for x in r.results[:3]]
    v = search(snap, "which code checks that an order is valid", rep)
    assert v.results[0].name == "validate_order"
    assert all(x.matched for x in v.results)  # every result explains itself


def test_filters_pagination_and_empty_results(layered):
    _, snap, rep = layered
    only_files = search(snap, "order", rep, labels=["File"])
    assert only_files.results and all(x.label == "File" for x in only_files.results)
    data = search(snap, "order", rep, layers=["data"])
    assert data.results and all(x.layer == "data" for x in data.results)
    full = search(snap, "order", rep, limit=100)
    page2 = search(snap, "order", rep, limit=2, offset=2)
    assert [x.node_id for x in page2.results] == [x.node_id for x in full.results[2:4]]
    assert search(snap, "zebra quantum teleport", rep).total == 0
    assert search(snap, "where is the", rep).total == 0 and query_terms("where is the") == []


def test_search_is_honest_about_its_mode(layered):
    _, snap, rep = layered
    r = search(snap, "authentication", rep)
    assert r.mode == SEARCH_MODE == "lexical+graph" and r.semantic is False
    assert "not enabled" in r.note


def test_search_project_isolation(layered, offline_settings):
    _, snap, rep = layered
    other = snapshot_of(FIXTURE, offline_settings)
    ids = {x.node_id for x in search(other, "order total price", limit=100).results}
    assert ids and ids <= set(other.nodes) and not ids & set(snap.nodes)
