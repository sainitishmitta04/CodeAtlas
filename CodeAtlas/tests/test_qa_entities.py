"""Entity resolution for /ask: short names, intent words, and the selected-node fallback.
Offline: graph lookups are faked, so only the resolution logic is exercised."""

import pytest

from codeatlas.models import GNode, GraphPayload
from codeatlas.qa import answer as qa


@pytest.fixture
def graph(monkeypatch):
    """`index` maps a lower-cased search term to the (node, score) rows the graph would return."""
    state = {"index": {}, "focus": None}
    monkeypatch.setattr(qa.queries, "find_entities",
                        lambda pid, term: state["index"].get(term, []))
    monkeypatch.setattr(qa.queries, "get_node",
                        lambda label, nid: state["focus"])
    monkeypatch.setattr(qa, "_gnode", lambda node: node)
    return state


def test_short_name_is_searched(graph):
    graph["index"]["db"] = [("Module:db", 2)]
    assert qa.resolve_entity("p", "What could be affected if db changes?", None, None) == "Module:db"


def test_intent_words_are_not_searched(graph):
    terms = qa.candidate_terms("Which functions are connected to what depends on Cart?")
    assert terms == ["Cart"]


def test_explicit_name_beats_selected_node(graph):
    graph["index"]["sync_project"] = [("Function:sync_project", 3)]
    graph["focus"] = "File:other.py"
    assert qa.resolve_entity("p", "What uses sync_project?", "File", "x") == "Function:sync_project"


def test_selected_node_used_when_question_has_no_name(graph):
    graph["focus"] = "File:graph/db.py"
    assert qa.resolve_entity("p", "What could be affected if this changes?", "File", "x") == \
        "File:graph/db.py"


def test_weak_match_does_not_override_selected_node(graph):
    graph["index"]["happen"] = [("File:random.py", 1)]
    graph["focus"] = "File:graph/db.py"
    assert qa.resolve_entity("p", "What would happen to this?", "File", "x") == "File:graph/db.py"


def test_weak_code_like_match_used_without_selection(graph):
    graph["index"]["db.py"] = [("File:graph/db.py", 1)]
    assert qa.resolve_entity("p", "What depends on db.py?", None, None) == "File:graph/db.py"


def test_nothing_found_and_nothing_selected_asks_for_clarification(graph):
    assert qa.resolve_entity("p", "What depends on the thing?", None, None) is None


def test_answer_question_clarifies_only_when_unresolved(graph, monkeypatch):
    monkeypatch.setattr(qa.queries, "project", lambda pid: {"id": pid, "name": "shop",
                                                            "sync_status": "complete"})
    monkeypatch.setattr(qa, "_retrieve", lambda intent, pid, entity: (["fact"], GraphPayload(), []))
    monkeypatch.setattr(qa, "chat", lambda system, user: "answer")
    monkeypatch.setattr(qa, "_gnode", lambda node: GNode(id=node, label="Module", caption=node))
    graph["index"]["api"] = [("Module:api", 2)]
    ans = qa.answer_question("p", "What depends on api?")
    assert ans.kind != "clarification" and ans.answer == "answer"
    ans = qa.answer_question("p", "What depends on the thing?")
    assert ans.kind == "clarification"
