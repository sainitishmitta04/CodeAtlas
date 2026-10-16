"""Convert raw Neo4j driver values (Node / Relationship / Path) into a UI graph payload.

Nothing here invents nodes or edges: every element comes from a query result.
"""

from collections.abc import Iterable
from typing import Any

from neo4j.graph import Node, Path, Relationship

from codeatlas.models import GEdge, GNode, GraphPayload

LABEL_PRIORITY = ("Project", "File", "Class", "Function", "Module")
HIDDEN_PROPS = {"sync_lock_touch", "sync_heartbeat"}


def _primary_label(node: Node) -> str:
    labels = set(node.labels)
    return next((label for label in LABEL_PRIORITY if label in labels), next(iter(labels), "Node"))


def _caption(label: str, props: dict[str, Any]) -> str:
    if label == "Function":
        qual = props.get("qualname") or props.get("name", "")
        return f"{qual}()"
    if label == "File":
        return props.get("path") or props.get("name", "")
    return str(props.get("name") or props.get("id", ""))


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return str(value)  # temporal/spatial driver types


class PayloadBuilder:
    def __init__(self) -> None:
        self._nodes: dict[str, GNode] = {}  # element_id -> node
        self._rels: dict[str, Relationship] = {}
        self.cypher: list[str] = []
        self.params: dict[str, Any] = {}

    def add(self, value: Any) -> None:
        if isinstance(value, Path):
            for n in value.nodes:
                self._add_node(n)
            for r in value.relationships:
                self._rels[r.element_id] = r
        elif isinstance(value, Node):
            self._add_node(value)
        elif isinstance(value, Relationship):
            self._rels[value.element_id] = value
            for n in (value.start_node, value.end_node):
                if n is not None and n.labels:
                    self._add_node(n)
        elif isinstance(value, dict):
            for v in value.values():
                self.add(v)
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            for v in value:
                self.add(v)

    def add_records(self, records: Iterable[dict], cypher: str, params: dict | None = None) -> None:
        for rec in records:
            self.add(rec)
        self.cypher.append(cypher.strip())
        if params:
            self.params.update({k: v for k, v in params.items() if k != "rows"})

    def _add_node(self, node: Node) -> None:
        if node.element_id in self._nodes or not node.labels:
            return
        props = {k: _clean(v) for k, v in dict(node).items() if k not in HIDDEN_PROPS}
        label = _primary_label(node)
        self._nodes[node.element_id] = GNode(
            id=str(props.get("id", node.element_id)), label=label,
            caption=_caption(label, props), props=props)

    def build(self, limit_hit: bool = False, note: str | None = None) -> GraphPayload:
        edges: dict[str, GEdge] = {}
        for r in self._rels.values():
            src = self._nodes.get(r.start_node.element_id) if r.start_node else None
            dst = self._nodes.get(r.end_node.element_id) if r.end_node else None
            if src is None or dst is None:
                continue  # endpoint not part of the result; never fabricate it
            eid = f"{src.id}|{r.type}|{dst.id}"
            edges[eid] = GEdge(id=eid, type=r.type, source=src.id, target=dst.id,
                               props={k: _clean(v) for k, v in dict(r).items()
                                      if k != "last_run_id"})
        return GraphPayload(nodes=list(self._nodes.values()), edges=list(edges.values()),
                            cypher=self.cypher, params=_clean_params(self.params),
                            truncated=limit_hit, note=note)


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: _clean(v) for k, v in params.items()}


def merge_payloads(base: GraphPayload, extra: GraphPayload) -> GraphPayload:
    nodes = {n.id: n for n in base.nodes}
    nodes.update({n.id: n for n in extra.nodes})
    edges = {e.id: e for e in base.edges}
    edges.update({e.id: e for e in extra.edges})
    return GraphPayload(nodes=list(nodes.values()), edges=list(edges.values()),
                        cypher=base.cypher + [c for c in extra.cypher if c not in base.cypher],
                        params={**base.params, **extra.params},
                        truncated=base.truncated or extra.truncated, note=extra.note or base.note)
