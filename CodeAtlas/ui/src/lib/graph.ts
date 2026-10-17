import type { GraphPayload, GNode } from "./types";

export const EMPTY_GRAPH: GraphPayload = {
  nodes: [], edges: [], cypher: [], params: {}, truncated: false, note: null,
  next_cursor: null, complete: null, page_nodes: null, totals: null,
};

/** Union of two payloads by node/edge id (the backend pages may share boundary nodes). */
export function mergeGraphs(base: GraphPayload | null, extra: GraphPayload): GraphPayload {
  if (!base) return extra;
  const nodes = new Map(base.nodes.map((n) => [n.id, n]));
  for (const n of extra.nodes) nodes.set(n.id, n);
  const edges = new Map(base.edges.map((e) => [e.id, e]));
  for (const e of extra.edges) edges.set(e.id, e);
  const cypher = [...base.cypher, ...extra.cypher.filter((c) => !base.cypher.includes(c))];
  return { ...base, nodes: [...nodes.values()], edges: [...edges.values()], cypher };
}

/** Drop nodes (and their edges) from a payload. */
export function removeNodes(g: GraphPayload, ids: Set<string>): GraphPayload {
  return {
    ...g,
    nodes: g.nodes.filter((n) => !ids.has(n.id)),
    edges: g.edges.filter((e) => !ids.has(e.source) && !ids.has(e.target)),
  };
}

/** Edges whose endpoints are both present (others cannot be drawn). */
export function drawableEdges(g: GraphPayload) {
  const ids = new Set(g.nodes.map((n) => n.id));
  return g.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
}

export function nodeTitle(n: GNode): string {
  const p = n.props;
  return String(p.qualname ?? p.path ?? p.name ?? n.caption);
}
