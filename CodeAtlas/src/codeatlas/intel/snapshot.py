"""An in-memory, read-only view of one project's graph.

Built either from Neo4j (`load_snapshot`, two bounded reads, cached per sync run) or from
analysis rows (`Snapshot.from_rows`, used by offline tests and dry runs). All Phase 2
features work on this structure, so they never issue per-node (N+1) queries.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from codeatlas.graph import db, queries
from codeatlas.graph.payload import _clean
from codeatlas.graph.sync import REL_VARIANTS
from codeatlas.models import GraphRows

MAX_SNAPSHOT_NODES = 30_000  # projects are capped at 25k nodes at sync time


class ProjectNotFound(LookupError):
    pass


class SnapshotTooLarge(RuntimeError):
    pass


@dataclass
class Edge:
    source: str
    type: str
    target: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    project_id: str
    project_name: str
    nodes: dict[str, dict[str, Any]]  # id -> props (incl. "label")
    edges: list[Edge]
    out: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))
    inc: dict[str, list[Edge]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self):
        for e in self.edges:
            self.out[e.source].append(e)
            self.inc[e.target].append(e)

    # ------------------------------------------------------------- construction
    @classmethod
    def from_rows(cls, pid: str, name: str, rows: GraphRows) -> "Snapshot":
        nodes = {}
        for label, node_rows in rows.nodes.items():
            for r in node_rows:
                nodes[r.id] = {**r.props, "id": r.id, "label": label}
        edges = [Edge(r.source, REL_VARIANTS[variant][1], r.target, dict(r.props))
                 for variant, rel_rows in rows.rels.items() if variant != "CONTAINS"
                 for r in rel_rows]
        return cls(pid, name, nodes, edges)

    # ------------------------------------------------------------- helpers
    def label(self, nid: str) -> str | None:
        n = self.nodes.get(nid)
        return n["label"] if n else None

    def of_label(self, label: str) -> list[dict[str, Any]]:
        return sorted((n for n in self.nodes.values() if n["label"] == label),
                      key=lambda n: n["id"])

    def out_edges(self, nid: str, rel: str | None = None) -> list[Edge]:
        return [e for e in self.out.get(nid, []) if rel is None or e.type == rel]

    def in_edges(self, nid: str, rel: str | None = None) -> list[Edge]:
        return [e for e in self.inc.get(nid, []) if rel is None or e.type == rel]

    def file_of(self, nid: str) -> str | None:
        """File node id containing a symbol (via DEFINES / HAS_METHOD), or itself."""
        n = self.nodes.get(nid)
        if not n:
            return None
        if n["label"] == "File":
            return nid
        for e in self.in_edges(nid):
            if e.type == "DEFINES":
                return e.source
            if e.type == "HAS_METHOD":
                return self.file_of(e.source)
        path = n.get("file_path")
        return next((f["id"] for f in self.of_label("File") if f.get("path") == path), None)

    def location(self, nid: str) -> dict[str, Any] | None:
        n = self.nodes.get(nid) or {}
        path = n.get("file_path") or n.get("path")
        if not path:
            return None
        return {"file_path": path, "start_line": n.get("start_line"),
                "end_line": n.get("end_line")}

    def display(self, nid: str) -> str:
        n = self.nodes.get(nid) or {}
        label = n.get("label")
        if label == "File":
            return n.get("path", nid)
        if label in ("Class", "Function"):
            return f"{n.get('file_path')}::{n.get('qualname') or n.get('name')}"
        return n.get("name", nid)


# ------------------------------------------------------------------ loading from Neo4j

_cache: dict[tuple[str, Any], Snapshot] = {}
_lock = Lock()


def load_snapshot(pid: str) -> Snapshot:
    proj = queries.project(pid)
    if proj is None:
        raise ProjectNotFound(f"Unknown project {pid}")
    key = (pid, proj.get("last_run_id") or proj.get("synced_at"))
    with _lock:
        if key in _cache:
            return _cache[key]
    nodes: dict[str, dict[str, Any]] = {}
    for rec in db.read(queries.SNAPSHOT_NODES, {"pid": pid}):
        props = {k: _clean(v) for k, v in rec["props"].items()}
        nodes[props["id"]] = {**props, "label": rec["label"]}
        if len(nodes) > MAX_SNAPSHOT_NODES:
            raise SnapshotTooLarge(f"Project {pid} has more than {MAX_SNAPSHOT_NODES} nodes; "
                                   "architecture analysis is limited to smaller projects.")
    edges = [Edge(r["s"], r["t"], r["d"], {k: _clean(v) for k, v in (r["props"] or {}).items()
                                            if k != "last_run_id"})
             for r in db.read(queries.snapshot_rels_query(), {"pid": pid})
             if r["s"] in nodes and r["d"] in nodes]
    snap = Snapshot(pid, proj.get("name") or pid, nodes, edges)
    with _lock:
        if len(_cache) >= 4:
            _cache.pop(next(iter(_cache)))
        _cache[key] = snap
    return snap
