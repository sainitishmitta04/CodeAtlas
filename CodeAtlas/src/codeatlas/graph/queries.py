"""Read-only, parameterized Cypher catalog.

Rules:
- Every query is anchored on a label plus an indexed property (`id` or `project_id`);
  there are no label-less MATCH clauses.
- Labels, relationship types and traversal depths are interpolated ONLY from the
  whitelists below after validation; all user values are parameters.
- Every Project / File / Module / Class / Function node carries `project_id`
  (for Project it equals `id`), so neighbourhood scoping works for any selected node.
"""

from typing import Any

from codeatlas.graph import db
from codeatlas.graph.payload import PayloadBuilder
from codeatlas.models import GraphPayload

LABELS = ("Project", "File", "Module", "Class", "Function")
REL_TYPES = ("CONTAINS", "DEFINES", "HAS_METHOD", "IMPORTS", "INHERITS", "CALLS")
MAX_DEPTH = 3
MAX_PATH_HOPS = 6
MAX_LIMIT = 400


class QueryInputError(ValueError):
    pass


def _label(label: str) -> str:
    if label not in LABELS:
        raise QueryInputError(f"Unknown node label {label!r}; expected one of {LABELS}")
    return label


def _rel_types(types: list[str] | None) -> list[str]:
    types = list(types or REL_TYPES)
    bad = [t for t in types if t not in REL_TYPES]
    if bad:
        raise QueryInputError(f"Unknown relationship types {bad}; expected {REL_TYPES}")
    return types


def _depth(depth: int, maximum: int = MAX_DEPTH) -> int:
    if not 1 <= int(depth) <= maximum:
        raise QueryInputError(f"depth must be between 1 and {maximum}")
    return int(depth)


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _anchor(label: str, param: str = "id") -> str:
    return f"(n:{_label(label)} {{id: ${param}}})"


def _payload(query: str, params: dict[str, Any], limit: int | None = None,
             note: str | None = None) -> GraphPayload:
    records = db.read(query, params)
    b = PayloadBuilder()
    b.add_records(records, query, params)
    return b.build(limit_hit=limit is not None and len(records) >= limit, note=note)


# ------------------------------------------------------------------ projects / stats

LIST_PROJECTS = """
MATCH (p:Project) WHERE p.source = 'codeatlas'
RETURN p.id AS id, p.name AS name, p.root_path AS root_path, p.sync_status AS sync_status,
       p.synced_at AS synced_at, p.file_count AS file_count, p.sync_error AS sync_error,
       p.source_kind AS source_kind, p.source_label AS source_label
ORDER BY p.name
"""

PROJECT = "MATCH (p:Project {id: $pid}) RETURN p"

PROJECTS_BY_IDENTITY = """
MATCH (p:Project) WHERE p.source = 'codeatlas' AND p.source_identity = $identity
RETURN p.id AS id ORDER BY p.synced_at DESC LIMIT 5
"""

NODE_STATS = """
MATCH (n:Project {id: $pid}) RETURN 'Project' AS label, count(n) AS count
UNION ALL MATCH (n:File {project_id: $pid}) RETURN 'File' AS label, count(n) AS count
UNION ALL MATCH (n:Module {project_id: $pid}) RETURN 'Module' AS label, count(n) AS count
UNION ALL MATCH (n:Class {project_id: $pid}) RETURN 'Class' AS label, count(n) AS count
UNION ALL MATCH (n:Function {project_id: $pid}) RETURN 'Function' AS label, count(n) AS count
"""

REL_STATS = """
MATCH (:Project {id: $pid})-[r:CONTAINS]->(:File) RETURN 'CONTAINS' AS type, count(r) AS count
UNION ALL MATCH (:File {project_id: $pid})-[r:DEFINES]->() RETURN 'DEFINES' AS type, count(r) AS count
UNION ALL MATCH (:Class {project_id: $pid})-[r:HAS_METHOD]->(:Function) RETURN 'HAS_METHOD' AS type, count(r) AS count
UNION ALL MATCH (:File {project_id: $pid})-[r:IMPORTS]->() RETURN 'IMPORTS' AS type, count(r) AS count
UNION ALL MATCH (:Class {project_id: $pid})-[r:INHERITS]->(:Class) RETURN 'INHERITS' AS type, count(r) AS count
UNION ALL MATCH (:Function {project_id: $pid})-[r:CALLS]->(:Function) RETURN 'CALLS' AS type, count(r) AS count
"""

CALL_CONFIDENCE = """
MATCH (:Function {project_id: $pid})-[r:CALLS]->(:Function)
RETURN r.confidence AS confidence, count(r) AS edges, sum(r.count) AS call_sites
ORDER BY confidence
"""

IMPORT_RESOLUTION = """
MATCH (:File {project_id: $pid})-[r:IMPORTS]->(t)
RETURN r.resolution AS resolution, count(r) AS edges, sum(r.count) AS statements
ORDER BY resolution
"""

TOP_IMPORTED_FILES = """
MATCH (f:File {project_id: $pid})
WITH f, COUNT { (:File)-[:IMPORTS]->(f) } AS importers,
     COUNT { (f)-[:DEFINES]->() } AS symbols
RETURN f.path AS path, importers, symbols, f.docstring AS docstring
ORDER BY importers DESC, symbols DESC, path LIMIT $limit
"""

TOP_CALLED_FUNCTIONS = """
MATCH (fn:Function {project_id: $pid})
WITH fn, COUNT { (:Function)-[:CALLS]->(fn) } AS callers
WHERE callers > 0
RETURN fn.qualname AS function, fn.file_path AS file, callers
ORDER BY callers DESC, function LIMIT $limit
"""

LIST_FILES = """
MATCH (f:File {project_id: $pid}) RETURN f.id AS id, f.path AS path ORDER BY path LIMIT 5000
"""

LIST_FUNCTIONS = """
MATCH (fn:Function {project_id: $pid})
RETURN fn.id AS id, fn.qualname AS qualname, fn.file_path AS file ORDER BY file, qualname LIMIT 5000
"""

INTEGRITY_CHECKS = {
    "files_without_single_contains": """
        MATCH (f:File {project_id: $pid})
        WHERE COUNT { (:Project {id: $pid})-[:CONTAINS]->(f) } <> 1
        RETURN count(f) AS violations""",
    "functions_without_single_defines": """
        MATCH (s:Function {project_id: $pid})
        WHERE COUNT { (:File {project_id: $pid})-[:DEFINES]->(s) } <> 1
        RETURN count(s) AS violations""",
    "classes_without_single_defines": """
        MATCH (s:Class {project_id: $pid})
        WHERE COUNT { (:File {project_id: $pid})-[:DEFINES]->(s) } <> 1
        RETURN count(s) AS violations""",
    "methods_without_is_method": """
        MATCH (:Class {project_id: $pid})-[:HAS_METHOD]->(m:Function)
        WHERE m.is_method <> true RETURN count(m) AS violations""",
    "cross_project_relationships": """
        MATCH (a:File {project_id: $pid})-[r]-(b) WHERE b.project_id <> $pid
        RETURN count(r) AS violations""",
    "cross_project_calls": """
        MATCH (a:Function {project_id: $pid})-[r:CALLS]-(b:Function) WHERE b.project_id <> $pid
        RETURN count(r) AS violations""",
    "stale_nodes": """
        MATCH (p:Project {id: $pid})
        MATCH (f:File {project_id: $pid}) WHERE f.last_run_id <> p.last_run_id
        RETURN count(f) AS violations""",
}


def list_projects() -> list[dict]:
    return db.read(LIST_PROJECTS)


def projects_by_identity(identity: str) -> list[str]:
    return [r["id"] for r in db.read(PROJECTS_BY_IDENTITY, {"identity": identity})]


def project(pid: str) -> dict | None:
    rows = db.read(PROJECT, {"pid": pid})
    if not rows:
        return None
    return {k: v for k, v in dict(rows[0]["p"]).items()
            if k not in ("sync_lock_touch", "sync_heartbeat")}


def stats(pid: str) -> dict:
    p = {"pid": pid, "limit": 10}
    return {
        "nodes": {r["label"]: r["count"] for r in db.read(NODE_STATS, p)},
        "relationships": {r["type"]: r["count"] for r in db.read(REL_STATS, p)},
        "call_confidence": db.read(CALL_CONFIDENCE, p),
        "import_resolution": db.read(IMPORT_RESOLUTION, p),
        "top_imported_files": db.read(TOP_IMPORTED_FILES, p),
        "top_called_functions": db.read(TOP_CALLED_FUNCTIONS, p),
        "cypher": [NODE_STATS.strip(), REL_STATS.strip()],
    }


def integrity(pid: str) -> dict[str, int]:
    return {name: db.read(q, {"pid": pid})[0]["violations"] for name, q in INTEGRITY_CHECKS.items()}


def list_files(pid: str) -> list[dict]:
    return db.read(LIST_FILES, {"pid": pid})


def list_functions(pid: str) -> list[dict]:
    return db.read(LIST_FUNCTIONS, {"pid": pid})


# ------------------------------------------------------------------ graph views

def overview_query(labels: list[str]) -> str:
    branches = []
    for label in labels:
        _label(label)
        anchor = "{id: $pid}" if label == "Project" else "{project_id: $pid}"
        branches.append(f"MATCH (n:{label} {anchor}) RETURN n")
    union = "\n  UNION ALL ".join(branches)
    return f"""
CALL {{
  {union}
}}
WITH n, COUNT {{ (n)--() }} AS degree
ORDER BY degree DESC, n.id
LIMIT $limit
WITH collect(n) AS ns
UNWIND ns AS a
OPTIONAL MATCH (a)-[r]->(b)
WHERE b IN ns AND type(r) IN $rel_types
RETURN ns AS nodes, collect(r) AS rels
"""


def overview(pid: str, labels: list[str] | None = None, rel_types: list[str] | None = None,
             limit: int = 150) -> GraphPayload:
    labels = list(labels or LABELS)
    q = overview_query(labels)
    limit = _limit(limit)
    params = {"pid": pid, "limit": limit, "rel_types": _rel_types(rel_types)}
    records = db.read(q, params)
    b = PayloadBuilder()
    b.add_records(records, q, params)
    n_nodes = len(records[0]["nodes"]) if records else 0
    return b.build(limit_hit=n_nodes >= limit,
                   note="Showing the most-connected nodes; expand nodes to see more."
                   if n_nodes >= limit else None)


def neighborhood_query(label: str, depth: int) -> str:
    return f"""
MATCH {_anchor(label)}
MATCH p = (n)-[rs*1..{_depth(depth)}]-(x)
WHERE all(r IN rs WHERE type(r) IN $rel_types) AND x.project_id = n.project_id
RETURN p
LIMIT $limit
"""


def neighborhood(label: str, node_id: str, depth: int = 1, rel_types: list[str] | None = None,
                 limit: int = 100) -> GraphPayload:
    center_q = f"MATCH {_anchor(label)} RETURN n"
    q = neighborhood_query(label, depth)
    limit = _limit(limit)
    params = {"id": node_id, "rel_types": _rel_types(rel_types), "limit": limit}
    b = PayloadBuilder()
    b.add_records(db.read(center_q, {"id": node_id}), center_q, {"id": node_id})
    records = db.read(q, params)
    b.add_records(records, q, params)
    return b.build(limit_hit=len(records) >= limit)


PATH_QUERIES = {
    # directed file dependency chain
    "dependency": """
MATCH (a:File {id: $from_id})
MATCH (b:File {id: $to_id})
MATCH p = shortestPath((a)-[:IMPORTS*1..%d]->(b))
RETURN p
""",
    # directed call chain
    "call": """
MATCH (a:Function {id: $from_id})
MATCH (b:Function {id: $to_id})
MATCH p = shortestPath((a)-[:CALLS*1..%d]->(b))
RETURN p
""",
    # any structural connection (excluding CONTAINS, which would trivially go via Project)
    "connection": """
MATCH (a:%s {id: $from_id})
MATCH (b:%s {id: $to_id})
MATCH p = shortestPath((a)-[:IMPORTS|CALLS|DEFINES|HAS_METHOD|INHERITS*1..%d]-(b))
RETURN p
""",
}


def path_between(mode: str, from_id: str, to_id: str, from_label: str = "File",
                 to_label: str = "File", max_hops: int = MAX_PATH_HOPS) -> GraphPayload:
    if from_id == to_id:
        raise QueryInputError("Choose two different nodes.")
    hops = _depth(max_hops, MAX_PATH_HOPS)
    if mode == "connection":
        q = PATH_QUERIES[mode] % (_label(from_label), _label(to_label), hops)
    elif mode in PATH_QUERIES:
        q = PATH_QUERIES[mode] % hops
    else:
        raise QueryInputError("mode must be dependency, call or connection")
    params = {"from_id": from_id, "to_id": to_id}
    payload = _payload(q, params)
    if payload.edges or mode == "connection":
        if not payload.edges:
            payload.note = f"No connection within {hops} hops."
        return payload
    reverse = _payload(q, {"from_id": to_id, "to_id": from_id})
    if reverse.edges:
        reverse.note = "No path in the requested direction; showing the reverse direction."
        return reverse
    payload.note = f"No {mode} path within {hops} hops in either direction."
    return payload


# ------------------------------------------------------------------ change impact
# File-level and function-level impact are separate questions with separate queries.

IMPACT_DEFINED = """
MATCH (f:File {id: $id})-[d:DEFINES]->(s)
RETURN f, d, s
"""

# files that import the changed file, directly or transitively (static imports only)
IMPACT_IMPORTERS = """
MATCH (f:File {id: $id})
MATCH p = (dep:File)-[:IMPORTS*1..%d]->(f)
WHERE dep <> f
WITH dep, p ORDER BY length(p)
WITH dep, collect(p)[0] AS shortest
RETURN dep.path AS file, length(shortest) AS hops, shortest AS p
ORDER BY hops, file LIMIT $limit
"""

# functions outside the file that (transitively) call functions defined in it
IMPACT_CALLERS_OF_FILE = """
MATCH (f:File {id: $id})-[:DEFINES]->(target:Function)
MATCH p = (caller:Function)-[:CALLS*1..%d]->(target)
MATCH (cf:File)-[:DEFINES]->(caller)
WHERE cf <> f
WITH caller, cf, p ORDER BY length(p)
WITH caller, cf, collect(p)[0] AS shortest
RETURN caller.qualname AS function, cf.path AS file, length(shortest) AS hops,
       [r IN relationships(shortest) | r.confidence] AS confidences, shortest AS p
ORDER BY hops, file, function LIMIT $limit
"""

# subclasses (in other files) of classes defined in the file
IMPACT_SUBCLASSES_OF_FILE = """
MATCH (f:File {id: $id})-[:DEFINES]->(c:Class)
MATCH p = (sub:Class)-[:INHERITS*1..%d]->(c)
MATCH (sf:File)-[:DEFINES]->(sub)
WHERE sf <> f
RETURN sub.qualname AS class, sf.path AS file, length(p) AS hops,
       [r IN relationships(p) | r.confidence] AS confidences, p
ORDER BY hops, file LIMIT $limit
"""

IMPACT_CALLERS_OF_FUNCTION = """
MATCH (target:Function {id: $id})
MATCH p = (caller:Function)-[:CALLS*1..%d]->(target)
WHERE caller <> target
WITH caller, p ORDER BY length(p)
WITH caller, collect(p)[0] AS shortest
MATCH (cf:File)-[:DEFINES]->(caller)
RETURN caller.qualname AS function, cf.path AS file, length(shortest) AS hops,
       [r IN relationships(shortest) | r.confidence] AS confidences, shortest AS p
ORDER BY hops, file, function LIMIT $limit
"""

IMPACT_CALLERS_OF_CLASS = """
MATCH (c:Class {id: $id})-[:HAS_METHOD]->(m:Function)
MATCH p = (caller:Function)-[:CALLS*1..%d]->(m)
MATCH (cf:File)-[:DEFINES]->(caller)
WHERE NOT (c)-[:HAS_METHOD]->(caller)
WITH caller, cf, p ORDER BY length(p)
WITH caller, cf, collect(p)[0] AS shortest
RETURN caller.qualname AS function, cf.path AS file, length(shortest) AS hops,
       [r IN relationships(shortest) | r.confidence] AS confidences, shortest AS p
ORDER BY hops, file, function LIMIT $limit
"""

IMPACT_SUBCLASSES_OF_CLASS = """
MATCH (c:Class {id: $id})
MATCH p = (sub:Class)-[:INHERITS*1..%d]->(c)
MATCH (sf:File)-[:DEFINES]->(sub)
RETURN sub.qualname AS class, sf.path AS file, length(p) AS hops,
       [r IN relationships(p) | r.confidence] AS confidences, p
ORDER BY hops, file LIMIT $limit
"""

IMPACT_IMPORTERS_OF_MODULE = """
MATCH (m:Module {id: $id})
MATCH p = (dep:File)-[:IMPORTS]->(m)
RETURN dep.path AS file, 1 AS hops, p
ORDER BY file LIMIT $limit
"""

COVERAGE_NOTES = [
    "File-level impact follows statically resolved IMPORTS between scanned project files. "
    "Dynamic imports (importlib, __import__), plugin/entry-point loading and imports that "
    "could not be mapped to a file are not covered.",
    "Function-level impact follows CALLS edges only. 'exact' edges come from same-file "
    "definitions, explicitly imported names, module attributes, self/cls methods and "
    "constructors; 'name_match' edges are unique-name guesses. Calls on objects of unknown "
    "type (obj.method()), callbacks, decorators, getattr and other dynamic dispatch are not "
    "in the graph, so affected functions may be missing.",
    "Importing a file does not prove the changed lines are used; dependents are candidates "
    "for review, not confirmed breakages.",
]


def _rows_and_paths(query: str, params: dict, builder: PayloadBuilder) -> list[dict]:
    records = db.read(query, params)
    builder.add_records([{"p": r["p"]} for r in records], query, params)
    return [{k: v for k, v in r.items() if k != "p"} for r in records]


def impact(label: str, node_id: str, depth: int = 2, limit: int = 50) -> dict:
    label, d, limit = _label(label), _depth(depth), _limit(limit)
    params = {"id": node_id, "limit": limit}
    b = PayloadBuilder()
    result: dict[str, Any] = {"target": {"label": label, "id": node_id}, "depth": d,
                              "defined_symbols": [], "importing_files": [],
                              "calling_functions": [], "subclasses": [],
                              "coverage_notes": COVERAGE_NOTES}
    center_q = f"MATCH {_anchor(label)} RETURN n"
    b.add_records(db.read(center_q, {"id": node_id}), center_q, {"id": node_id})

    if label == "File":
        defined = db.read(IMPACT_DEFINED, params)
        b.add_records(defined, IMPACT_DEFINED, params)
        result["defined_symbols"] = sorted(
            f"{'class' if 'Class' in r['s'].labels else 'function'} {r['s']['qualname']} "
            f"(lines {r['s']['start_line']}-{r['s']['end_line']})" for r in defined)
        result["importing_files"] = _rows_and_paths(IMPACT_IMPORTERS % d, params, b)
        result["calling_functions"] = _rows_and_paths(IMPACT_CALLERS_OF_FILE % d, params, b)
        result["subclasses"] = _rows_and_paths(IMPACT_SUBCLASSES_OF_FILE % d, params, b)
    elif label == "Function":
        result["calling_functions"] = _rows_and_paths(IMPACT_CALLERS_OF_FUNCTION % d, params, b)
    elif label == "Class":
        result["calling_functions"] = _rows_and_paths(IMPACT_CALLERS_OF_CLASS % d, params, b)
        result["subclasses"] = _rows_and_paths(IMPACT_SUBCLASSES_OF_CLASS % d, params, b)
    elif label == "Module":
        result["importing_files"] = _rows_and_paths(IMPACT_IMPORTERS_OF_MODULE, params, b)
    else:
        raise QueryInputError("Impact analysis supports File, Function, Class and Module nodes.")

    affected = {r["file"] for key in ("importing_files", "calling_functions", "subclasses")
                for r in result[key]}
    result["affected_files"] = sorted(affected)
    result["graph"] = b.build().model_dump()
    return result


# ------------------------------------------------------------------ entity lookup (Q&A)

FIND_ENTITIES = """
CALL {
  MATCH (n:File {project_id: $pid})
  WHERE toLower(n.path) CONTAINS $term OR toLower(n.module) = $term
  RETURN n, CASE WHEN toLower(n.path) = $term OR toLower(n.name) = $term
                      OR toLower(n.module) = $term THEN 3
                 WHEN toLower(n.path) ENDS WITH $term THEN 2 ELSE 1 END AS score
  UNION ALL
  MATCH (n:Class {project_id: $pid}) WHERE toLower(n.name) CONTAINS $term
  RETURN n, CASE WHEN toLower(n.name) = $term THEN 3 ELSE 1 END AS score
  UNION ALL
  MATCH (n:Function {project_id: $pid})
  WHERE toLower(n.name) CONTAINS $term OR toLower(n.qualname) = $term
  RETURN n, CASE WHEN toLower(n.qualname) = $term OR toLower(n.name) = $term THEN 3
                 ELSE 1 END AS score
  UNION ALL
  MATCH (n:Module {project_id: $pid}) WHERE toLower(n.name) = $term
  RETURN n, 2 AS score
}
RETURN n, score
ORDER BY score DESC, size(coalesce(n.path, n.qualname, n.name)) ASC
LIMIT $limit
"""


def find_entities(pid: str, term: str, limit: int = 5) -> list[tuple[Any, int]]:
    rows = db.read(FIND_ENTITIES, {"pid": pid, "term": term.lower().strip(), "limit": limit})
    return [(r["n"], r["score"]) for r in rows]


def get_node(label: str, node_id: str):
    rows = db.read(f"MATCH {_anchor(label)} RETURN n", {"id": node_id})
    return rows[0]["n"] if rows else None
