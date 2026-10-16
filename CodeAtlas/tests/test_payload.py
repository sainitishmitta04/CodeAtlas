from neo4j.graph import Graph, Node, Path

from codeatlas.graph.payload import PayloadBuilder


def _graph():
    g = Graph()
    f = Node(g, "e1", 1, ["File"], {"id": "p:file:a.py", "path": "a.py", "name": "a.py",
                                     "last_run_id": "r"})
    fn = Node(g, "e2", 2, ["Function"], {"id": "p:function:a.py:run", "qualname": "run",
                                          "name": "run"})
    rel_cls = g.relationship_type("DEFINES")
    r = rel_cls(g, "r1", 1, {"last_run_id": "r"})
    r._start_node, r._end_node = f, fn
    return f, fn, r


def test_nodes_and_edges_come_only_from_results():
    f, fn, r = _graph()
    b = PayloadBuilder()
    b.add_records([{"p": Path(f, r)}], "MATCH p=... RETURN p", {"pid": "p"})
    out = b.build()
    assert {(n.id, n.label, n.caption) for n in out.nodes} == {
        ("p:file:a.py", "File", "a.py"), ("p:function:a.py:run", "Function", "run()")}
    assert [(e.source, e.type, e.target) for e in out.edges] == [
        ("p:file:a.py", "DEFINES", "p:function:a.py:run")]
    assert out.cypher == ["MATCH p=... RETURN p"]


def test_edge_with_missing_endpoint_is_dropped_not_fabricated():
    f, fn, r = _graph()
    b = PayloadBuilder()
    b.add(f)          # only one endpoint returned as a node
    b._rels[r.element_id] = r
    # simulate a relationship whose end node was not part of the result
    b._nodes.pop("e2", None)
    assert b.build().edges == []
