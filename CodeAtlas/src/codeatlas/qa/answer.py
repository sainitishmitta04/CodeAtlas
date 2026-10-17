"""Grounded Q&A: question -> intent -> fixed Cypher retrieval -> bounded facts -> GPT-4o mini.

The LLM never writes Cypher. If it is unavailable, the retrieved facts are returned as-is,
so the answer is still grounded and browsing is unaffected.
"""

import re

from codeatlas.graph import queries
from codeatlas.graph.payload import PayloadBuilder, merge_payloads
from codeatlas.llm.client import LLMError, chat
from codeatlas.models import Answer, GNode, GraphPayload

MAX_FACT_NODES = 40
MAX_FACT_EDGES = 80
MAX_FACT_CHARS = 12_000

INTENT_PATTERNS = [
    ("impact", r"\b(affect|affected|impact|break|breaks|ripple|blast radius)\b|\bif\b.*\bchange"),
    ("dependents", r"\b(depends? on|dependents?|imported by|who (uses|imports|calls)|callers?|"
                   r"used by|calls? (this|it)|what uses)\b"),
    ("connected", r"\b(connected|related|neighbou?r|linked|around|interacts?)\b"),
    ("main_files", r"\b(main|core|key|important|central|entry ?points?)\b.*"
                   r"\b(files?|modules?|functionality|code|parts?)\b|\bwhich files\b"),
    ("overview", r"\b(what does|overview|purpose|about|summari[sz]e|explain the (repo|project))\b"),
]
ENTITY_INTENTS = {"impact", "dependents", "connected"}
STOPWORDS = set("""what which who whom does do is are the this that these those file files module
modules function functions class classes repository repo project code depends depend on of in
to for and or if change changes changed could be affected affect connected component with from
main contain contains functionality where how why when it its me show list tell about uses use
used calls call by related linked around neighbour neighbor neighbors interacts impact impacts
break breaks ripple radius blast dependents dependent imported imports importers import caller
callers called inherits inherit subclasses defines define symbols symbol any all have has
happen happens""".split())
SYSTEM_PROMPT = """You are CodeAtlas, an assistant that answers questions about a code repository
using ONLY the graph facts provided (retrieved from a Neo4j code graph).
Rules:
- Cite file paths (and line numbers when given) for every concrete claim.
- Structure the answer in two sections: "**Known from the graph**" (directly supported facts)
  and "**Hypotheses**" (inferences, e.g. purpose guessed from names or docstrings; label them).
- Never invent files, functions or relationships that are not in the facts.
- CALLS edges marked name_match are lower confidence; say so when you rely on them.
- If the facts are insufficient to answer, say exactly what is missing.
- Keep the answer under 250 words."""


def route(question: str) -> str:
    q = question.lower()
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, q):
            return intent
    return "overview"


def candidate_terms(question: str) -> list[str]:
    quoted = re.findall(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", question)
    terms = [t for group in quoted for t in group if t]
    for tok in re.findall(r"[A-Za-z_][\w./]*", question):
        tok = tok.strip("./")
        if len(tok) < 2 or tok.lower() in STOPWORDS:  # short names (db, api, cli) are valid
            continue
        terms.append(tok)
    seen, out = set(), []
    for t in terms:  # prefer path-like / code-like tokens first
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return sorted(out, key=lambda t: (not ("/" in t or t.endswith(".py")),
                                      not ("_" in t or "." in t)))[:6]


def _gnode(node) -> GNode:
    b = PayloadBuilder()
    b.add(node)
    return b.build().nodes[0]


def _code_like(term: str) -> bool:
    return bool("/" in term or "." in term or "_" in term or re.search(r"[a-z][A-Z]", term)
                or len(term) > 3)


def resolve_entity(pid: str, question: str, focus_label: str | None,
                   focus_id: str | None) -> GNode | None:
    """Pick the node a question is about. Order: an explicit name in the question (exact name
    or path suffix, score >= 2); else the node selected in the graph; else a weak substring
    match, but only for code-like words, so an ordinary word cannot pick a random file."""
    best, weak = None, None
    for term in candidate_terms(question):
        for node, score in queries.find_entities(pid, term):
            if score >= 2 and (best is None or score > best[1]):
                best = (node, score)
            if score < 2 and _code_like(term) and weak is None:
                weak = node
        if best and best[1] >= 3:
            break
    if best:
        return _gnode(best[0])
    if focus_label and focus_id:
        node = queries.get_node(focus_label, focus_id)
        if node is not None:
            return _gnode(node)
    return _gnode(weak) if weak is not None else None


# ------------------------------------------------------------------ fact rendering

def _ref(n: GNode) -> str:
    p = n.props
    if n.label in ("Function", "Class"):
        return f"{n.label} {p.get('file_path')}::{p.get('qualname')}"
    if n.label == "File":
        return f"File {p.get('path')}"
    if n.label == "Module":
        return f"Module {p.get('name')} ({p.get('kind')})"
    return f"{n.label} {p.get('name')}"


def _node_fact(n: GNode) -> str:
    p = n.props
    parts = [_ref(n)]
    if n.label in ("Function", "Class") and p.get("start_line"):
        parts.append(f"lines {p['start_line']}-{p['end_line']}")
    if n.label == "Function" and p.get("params"):
        parts.append(f"params ({', '.join(p['params'])})")
    if n.label == "File":
        parts.append(f"{p.get('loc')} lines, {p.get('symbol_count')} symbols")
    if p.get("docstring"):
        parts.append(f'doc: "{p["docstring"][:160]}"')
    return " | ".join(parts)


def _edge_fact(e, by_id: dict[str, GNode]) -> str:
    src, dst = by_id[e.source], by_id[e.target]
    p = e.props
    extra = []
    if p.get("lines"):
        extra.append("line" + ("s " if len(p["lines"]) > 1 else " ") + ",".join(map(str, p["lines"][:10])))
    if e.type == "CALLS" and p.get("confidence"):
        extra.append(f"confidence {p['confidence']}")
    if e.type == "IMPORTS" and p.get("names"):
        extra.append("names " + ",".join(p["names"][:8]))
    if e.type == "INHERITS" and p.get("confidence"):
        extra.append(f"confidence {p['confidence']}")
    suffix = f" ({'; '.join(extra)})" if extra else ""
    return f"{_ref(src)} -[{e.type}]-> {_ref(dst)}{suffix}"


def payload_facts(g: GraphPayload) -> list[str]:
    by_id = {n.id: n for n in g.nodes}
    facts = [_node_fact(n) for n in g.nodes[:MAX_FACT_NODES]]
    facts += [_edge_fact(e, by_id) for e in g.edges[:MAX_FACT_EDGES]]
    return facts


# ------------------------------------------------------------------ retrieval per intent

def _retrieve(intent: str, pid: str, entity: GNode | None) -> tuple[list[str], GraphPayload, list[str]]:
    facts: list[str] = []
    caveats: list[str] = []
    if intent in ("overview", "main_files"):
        st = queries.stats(pid)
        facts.append("Node counts: " + ", ".join(f"{k}={v}" for k, v in st["nodes"].items()))
        facts.append("Relationship counts: " + ", ".join(f"{k}={v}" for k, v in st["relationships"].items()))
        for r in st["top_imported_files"]:
            doc = f' doc: "{r["docstring"][:140]}"' if r.get("docstring") else ""
            facts.append(f"File {r['path']} is imported by {r['importers']} project files and "
                         f"defines {r['symbols']} symbols.{doc}")
        for r in st["top_called_functions"]:
            facts.append(f"Function {r['file']}::{r['function']} has {r['callers']} distinct callers.")
        g = queries.overview(pid, ["Project", "File", "Class"], ["CONTAINS", "IMPORTS", "DEFINES"], 60)
        facts += payload_facts(g)
        if intent == "overview":
            caveats.append("Repository purpose is inferred from file names, docstrings and "
                           "structure; no README or LLM summaries are stored in the graph.")
        return facts, g, caveats

    assert entity is not None
    if intent == "connected":
        g = queries.neighborhood(entity.label, entity.id, depth=1, limit=80)
        return payload_facts(g), g, caveats

    # impact / dependents share the impact traversal (dependents = depth 1)
    if entity.label == "Project":
        raise queries.QueryInputError("Pick a file, class, function or module for this question.")
    depth = 2 if intent == "impact" else 1
    res = queries.impact(entity.label, entity.id, depth=depth)
    g = GraphPayload.model_validate(res["graph"])
    facts.append(f"Target: {_ref(entity)}")
    for s in res["defined_symbols"]:
        facts.append(f"{_ref(entity)} defines {s}")
    for r in res["importing_files"]:
        facts.append(f"File {r['file']} imports the target (import hops: {r['hops']})")
    for r in res["calling_functions"]:
        facts.append(f"Function {r['file']}::{r['function']} calls into the target "
                     f"(call hops: {r['hops']}; confidences: {', '.join(r['confidences'])})")
    for r in res["subclasses"]:
        facts.append(f"Class {r['file']}::{r['class']} inherits from the target (hops: {r['hops']})")
    if res["affected_files"]:
        facts.append("Candidate affected files: " + ", ".join(res["affected_files"]))
    else:
        facts.append("No importing files, callers or subclasses were found in the graph.")
    facts += payload_facts(g)
    caveats += res["coverage_notes"]
    return facts, g, caveats


def _bounded(facts: list[str]) -> list[str]:
    out, total = [], 0
    for f in dict.fromkeys(facts):  # de-duplicate, keep order
        total += len(f) + 1
        if total > MAX_FACT_CHARS:
            break
        out.append(f)
    return out


class AnswerFailed(RuntimeError):
    """The graph was queried, but the LLM could not write the answer. Carries the retrieved
    facts/evidence so the UI can show them, clearly labelled as *not* an answer."""

    def __init__(self, error: LLMError, partial: Answer):
        super().__init__(error.message)
        self.error, self.partial = error, partial


def answer_question(pid: str, question: str, focus_label: str | None = None,
                    focus_id: str | None = None) -> Answer:
    proj = queries.project(pid)
    if proj is None:
        raise queries.QueryInputError(f"Unknown project {pid}")
    intent = route(question)
    entity = resolve_entity(pid, question, focus_label, focus_id) if intent in ENTITY_INTENTS else None
    if intent in ENTITY_INTENTS and entity is None:
        return Answer(intent=intent, answer=(
            "I could not identify which file, class or function you mean. Mention it by name "
            "(e.g. `graph/db.py` or `sync_project`) or select a node in the graph first."),
            llm_used=False, kind="clarification")

    facts, evidence, caveats = _retrieve(intent, pid, entity)
    if proj.get("sync_status") != "complete":
        caveats.insert(0, f"Project sync status is '{proj.get('sync_status')}'; the graph may be "
                          "partially updated. Re-run analysis to repair it.")
    facts = _bounded(facts)
    user = (f"Question: {question}\nIntent: {intent}\nProject: {proj.get('name')}\n\n"
            "Graph facts:\n" + "\n".join(f"- {f}" for f in facts) +
            "\n\nCoverage caveats:\n" + "\n".join(f"- {c}" for c in caveats))
    evidence = merge_payloads(GraphPayload(), evidence)
    try:
        text = chat(SYSTEM_PROMPT, user)
    except LLMError as exc:
        raise AnswerFailed(exc, Answer(intent=intent, entity=entity, answer="", llm_used=False,
                                       facts=facts, caveats=caveats, evidence=evidence)) from None
    return Answer(intent=intent, entity=entity, answer=text, llm_used=True, facts=facts,
                  caveats=caveats, evidence=evidence)
