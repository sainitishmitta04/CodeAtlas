"""Static checks of the Cypher catalog: whitelisting, anchoring and project scoping."""

import re

import pytest

from codeatlas.cli import catalog
from codeatlas.graph import queries
from codeatlas.qa.answer import candidate_terms, route


def test_input_validation():
    with pytest.raises(queries.QueryInputError):
        queries.neighborhood_query("File) DETACH DELETE (n", 1)
    with pytest.raises(queries.QueryInputError):
        queries.neighborhood_query("File", 4)
    with pytest.raises(queries.QueryInputError):
        queries._rel_types(["CALLS", "KNOWS"])
    with pytest.raises(queries.QueryInputError):
        queries.overview_query(["Person"])


@pytest.mark.parametrize("name,text", sorted(catalog().items()))
def test_no_label_less_match(name, text):
    """Every MATCH pattern must start from a labelled node (index-backed anchors)."""
    for m in re.finditer(r"MATCH\s+(?:p\s*=\s*)?(?:shortestPath\()?\(\s*(\w*)\s*(:?)", text):
        var, colon = m.groups()
        if name in ("node_total", "rel_total"):
            continue  # count-store lookups
        if not colon:
            # a bare variable is allowed only if it was bound earlier in the query
            before = text[:m.start()]
            bound = re.search(rf"\(\s*{var}\s*:", before) or re.search(rf"\bAS {var}\b", before)
            assert var and bound, (name, m.group(0))


@pytest.mark.parametrize("name,text", sorted(catalog().items()))
def test_destructive_queries_are_project_scoped(name, text):
    if "DELETE" in text:
        assert "$pid" in text and "$run_id" in text, name


def test_neighborhood_supports_every_label():
    for label in queries.LABELS:
        q = queries.neighborhood_query(label, 2)
        assert f"(n:{label} {{id: $id}})" in q and "x.project_id = n.project_id" in q


@pytest.mark.parametrize("question,intent", [
    ("What does this repository do?", "overview"),
    ("Which files contain the main functionality?", "main_files"),
    ("What depends on graph/db.py?", "dependents"),
    ("Which functions are connected to sync_project?", "connected"),
    ("What could be affected if graph/db.py changes?", "impact"),
])
def test_routing(question, intent):
    assert route(question) == intent


def test_candidate_terms_prefer_code_like_tokens():
    assert candidate_terms("What could be affected if `graph/db.py` changes?")[0] == "graph/db.py"
    assert candidate_terms("Which functions are connected to sync_project?")[0] == "sync_project"
