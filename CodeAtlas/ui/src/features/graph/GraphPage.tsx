import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../../context/AppContext";
import { api } from "../../lib/api";
import { mergeGraphs, removeNodes, drawableEdges } from "../../lib/graph";
import { isAbort } from "../../lib/errors";
import { buildHash, navigate } from "../../lib/router";
import { fmtNumber } from "../../lib/format";
import { LABELS, REL_TYPES, type GNode, type GraphPayload, type OverviewTotals } from "../../lib/types";
import { LABEL_COLORS, REL_COLORS } from "../../lib/theme";
import { PageHeader, Panel } from "../../components/layout/Page";
import { Graph } from "../../components/graph/LazyGraph";
import { GraphLegend } from "../../components/graph/Legend";
import { Badge, CypherBlock, EmptyState, ErrorState, Notice, Spinner } from "../../components/feedback/Feedback";
import { NodeProps } from "./NodeProps";

/** Pages load automatically up to this many nodes; the rest is an explicit action. */
const AUTO_LOAD_MAX_NODES = 2000;
const PAGE_SIZES = [100, 250, 500, 1000, 2000];

interface Meta {
  mode: "overview" | "neighbourhood";
  cursor: string | null;
  complete: boolean;
  loaded: number;
  totals: OverviewTotals | null;
  neighbourhoodOf?: string;
}

function Toggle({ on, label, color, onClick }: { on: boolean; label: string; color?: string; onClick: () => void }) {
  return (
    <button type="button" className={`toggle${on ? " is-on" : ""}`} aria-pressed={on} onClick={onClick}>
      <i style={{ background: on ? color : "transparent", borderColor: color }} />
      {label}
    </button>
  );
}

export function GraphPage() {
  const { pid, setFocus, focus, refreshKey } = useApp();
  const [labels, setLabels] = useState<string[]>([...LABELS]);
  const [rels, setRels] = useState<string[]>([...REL_TYPES]);
  const [pageSize, setPageSize] = useState(500);
  const [depth, setDepth] = useState(1);
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [meta, setMeta] = useState<Meta>({ mode: "overview", cursor: null, complete: false, loaded: 0, totals: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<GNode | null>(focus && focus.props.project_id === pid ? focus : null);
  const [query, setQuery] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  const graphRef = useRef<GraphPayload | null>(null);
  graphRef.current = graph;

  const loadPages = useCallback(
    async (start: { cursor: string | null; loaded: number; totals: OverviewTotals | null; base: GraphPayload | null }, untilNodes: number) => {
      if (!pid) return;
      ctrl.current?.abort();
      const c = new AbortController();
      ctrl.current = c;
      setLoading(true);
      setError(null);
      let { cursor, loaded, totals, base } = start;
      let complete = false;
      try {
        for (let i = 0; i < 1000; i++) {
          const page = await api.overview({ project_id: pid, labels, rel_types: rels, limit: pageSize, after: cursor ?? "" }, c.signal);
          base = mergeGraphs(base, page);
          totals = page.totals ?? totals;
          cursor = page.next_cursor;
          complete = !!page.complete;
          loaded += page.page_nodes ?? 0;
          setGraph(base);
          setMeta({ mode: "overview", cursor, complete, loaded, totals });
          if (complete || !cursor || loaded >= untilNodes) break;
        }
      } catch (err) {
        if (!isAbort(err)) setError(err);
      } finally {
        if (ctrl.current === c) setLoading(false);
      }
    },
    [pid, labels, rels, pageSize],
  );

  // (Re)load the overview whenever the selection changes.
  useEffect(() => {
    setGraph(null);
    setMeta({ mode: "overview", cursor: null, complete: false, loaded: 0, totals: null });
    if (labels.length) void loadPages({ cursor: null, loaded: 0, totals: null, base: null }, AUTO_LOAD_MAX_NODES);
    return () => ctrl.current?.abort();
  }, [loadPages, labels.length, refreshKey]);

  const loadMore = (all: boolean) =>
    loadPages({ cursor: meta.cursor, loaded: meta.loaded, totals: meta.totals, base: graphRef.current }, all ? Number.MAX_SAFE_INTEGER : meta.loaded + 1);

  const expand = async (node: GNode, only: boolean) => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    try {
      const more = await api.neighborhood({ label: node.label, node_id: node.id, depth, rel_types: rels, limit: only ? 120 : 80 }, c.signal);
      if (only) {
        setGraph(more);
        setMeta((m) => ({ ...m, mode: "neighbourhood", neighbourhoodOf: node.caption }));
      } else {
        setGraph((g) => mergeGraphs(g, more));
      }
      setFocusId(node.id);
    } catch (err) {
      if (!isAbort(err)) setError(err);
    } finally {
      if (ctrl.current === c) setLoading(false);
    }
  };

  const select = (n: GNode | null) => {
    setSelected(n);
    if (n) setFocus(n);
  };

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !graph) return null;
    return graph.nodes.filter((n) => n.caption.toLowerCase().includes(q) || String(n.props.path ?? n.props.qualname ?? "").toLowerCase().includes(q)).slice(0, 200);
  }, [query, graph]);
  const matchIds = useMemo(() => (matches ? new Set(matches.map((m) => m.id)) : null), [matches]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const totals = meta.totals;
  const shownNodes = graph?.nodes.length ?? 0;
  const shownEdges = graph ? drawableEdges(graph).length : 0;

  return (
    <>
      <PageHeader eyebrow="Explore" title="Project graph">
        Every node and relationship is read from Neo4j. The graph loads page by page until the selection is complete.
      </PageHeader>

      <div className="graph-layout reveal">
        <aside className="graph-controls" aria-label="Graph filters">
          <Panel tone="glass">
            <div className="field-label" id="nt-label">Node types</div>
            <div className="toggles" role="group" aria-labelledby="nt-label">
              {LABELS.map((l) => (
                <Toggle key={l} label={l} color={LABEL_COLORS[l]} on={labels.includes(l)} onClick={() => toggle(labels, setLabels, l)} />
              ))}
            </div>
            <div className="field-label" id="rt-label">Relationship types</div>
            <div className="toggles" role="group" aria-labelledby="rt-label">
              {REL_TYPES.map((r) => (
                <Toggle key={r} label={r} color={REL_COLORS[r]} on={rels.includes(r)} onClick={() => toggle(rels, setRels, r)} />
              ))}
            </div>
            <p className="hint">A relationship is drawn when both endpoint node types are selected.</p>
            <div className="field-row">
              <label className="field">
                <span className="field-label">Nodes per page</span>
                <select className="select" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                  {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <label className="field">
                <span className="field-label">Expansion depth</span>
                <select className="select" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                  {[1, 2, 3].map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </label>
            </div>
            <label className="field">
              <span className="field-label">Find in loaded graph</span>
              <input className="input" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="name or path" />
            </label>
            {matches && (
              <div className="match-list" role="listbox" aria-label="Matching nodes">
                <div className="hint">{matches.length === 200 ? "200+" : matches.length} match(es) in the loaded graph</div>
                {matches.slice(0, 12).map((m) => (
                  <button key={m.id} role="option" aria-selected={selected?.id === m.id} className="match" onClick={() => { select(m); setFocusId(m.id); }}>
                    <i style={{ background: LABEL_COLORS[m.label] }} />
                    <span className="mono">{m.caption}</span>
                  </button>
                ))}
              </div>
            )}
            <button className="btn btn--ghost btn--block" onClick={() => { setGraph(null); void loadPages({ cursor: null, loaded: 0, totals: null, base: null }, AUTO_LOAD_MAX_NODES); }}>
              Reload graph
            </button>
          </Panel>
        </aside>

        <div className="graph-main">
          <div className="graph-status" aria-live="polite">
            {meta.mode === "neighbourhood" ? (
              <Notice tone="info" title={`Neighbourhood of ${meta.neighbourhoodOf}`}
                action={<button className="btn btn--sm" onClick={() => { setGraph(null); void loadPages({ cursor: null, loaded: 0, totals: null, base: null }, AUTO_LOAD_MAX_NODES); }}>Back to complete graph</button>}>
                {shownNodes} nodes · {shownEdges} relationships — not the full graph.
              </Notice>
            ) : graph ? (
              meta.complete ? (
                <div className="completeness is-complete">
                  <Badge tone="sage">complete</Badge>
                  <span>Displayed <b>{fmtNumber(shownNodes)}</b> / {fmtNumber(totals?.selected_nodes)} nodes · <b>{fmtNumber(shownEdges)}</b> / {fmtNumber(totals?.selected_relationships)} relationships</span>
                </div>
              ) : (
                <div className="completeness is-partial">
                  <Badge tone="warn">partial</Badge>
                  <span>Displayed <b>{fmtNumber(shownNodes)}</b> / {fmtNumber(totals?.selected_nodes)} nodes · <b>{fmtNumber(shownEdges)}</b> / {fmtNumber(totals?.selected_relationships)} relationships
                    {totals ? ` · ${fmtNumber(Math.max(0, totals.selected_nodes - meta.loaded))} more nodes` : ""}</span>
                  <span className="completeness__actions">
                    <button className="btn btn--sm" disabled={loading} onClick={() => loadMore(false)}>Load next page</button>
                    <button className="btn btn--sm btn--ghost" disabled={loading} onClick={() => loadMore(true)} title="May be slow to lay out for very large graphs">Load all remaining</button>
                  </span>
                </div>
              )
            ) : null}
            {totals && meta.mode === "overview" && (
              <div className="hint">
                Project total: {fmtNumber(totals.nodes)} nodes, {fmtNumber(totals.relationships)} relationships.
                {totals.hidden_relationships > 0 && ` ${fmtNumber(totals.hidden_relationships)} relationships are hidden because an endpoint's node type is not selected.`}
              </div>
            )}
            {loading && <div className="loading-inline"><Spinner size={14} /> Reading from Neo4j…</div>}
          </div>
          {error != null && <ErrorState error={error} onRetry={() => loadMore(false)} />}

          <div className="graph-stage">
            {!labels.length ? (
              <EmptyState title="No node types selected">Select at least one node type to draw the graph.</EmptyState>
            ) : graph && graph.nodes.length ? (
              <Graph
                graph={graph}
                selectedId={selected?.id}
                onSelect={select}
                highlight={matchIds}
                focusId={focusId}
                height="min(72vh, 760px)"
                ariaLabel={`Project graph with ${shownNodes} nodes and ${shownEdges} relationships. Use the filters and the find box to select nodes.`}
              />
            ) : graph ? (
              <EmptyState title="No graph elements">Neo4j returned no nodes for this selection.</EmptyState>
            ) : (
              <div className="graph-canvas graph-canvas--loading" style={{ height: "min(72vh, 760px)" }}>
                <div className="graph-boot"><Spinner size={22} /><span>Loading the first page from Neo4j…</span></div>
              </div>
            )}
            {selected && (
              <aside className="inspector" aria-label="Selected node">
                <Panel tone="glass" className="inspector__panel">
              <div className="inspector__head">
                <span className="badge" style={{ color: LABEL_COLORS[selected.label], borderColor: LABEL_COLORS[selected.label] }}>{selected.label}</span>
                <button className="icon-btn" aria-label="Close node details" onClick={() => setSelected(null)}>×</button>
              </div>
              <h2 className="inspector__title mono">{selected.caption}</h2>
              <div className="inspector__actions">
                {selected.label !== "Project" && (
                  <a className="btn btn--primary btn--sm" href={buildHash("explain", pid, { node: selected.id })}>Explain</a>
                )}
                {["File", "Function", "Class", "Module"].includes(selected.label) && (
                  <a className="btn btn--sm" href={buildHash("paths", pid, { impact: selected.id, label: selected.label })}>Impact</a>
                )}
                <button className="btn btn--sm" disabled={loading} onClick={() => expand(selected, false)}>Expand neighbours</button>
                <button className="btn btn--sm btn--ghost" disabled={loading} onClick={() => expand(selected, true)}>Only this neighbourhood</button>
                <button className="btn btn--sm btn--ghost" onClick={() => { setGraph((g) => (g ? removeNodes(g, new Set([selected.id])) : g)); setSelected(null); }}>Hide</button>
                {selected.label === "Function" && (
                  <button className="btn btn--sm btn--ghost" onClick={() => navigate("tours", pid, { kind: "execution", target: selected.id, start: "1" })}>Execution tour</button>
                )}
              </div>
              <NodeProps props={selected.props} />
            </Panel>
              </aside>
            )}
            <GraphLegend labels={labels} rels={rels} />
            {!selected && graph && graph.nodes.length > 0 && <p className="hint">Select a node to inspect its stored properties, expand it from Neo4j or open it in Explain · hover highlights neighbours · drag to reposition · scroll to zoom.</p>}
          </div>
          {graph && <CypherBlock cypher={graph.cypher} params={graph.params} />}
        </div>


      </div>
    </>
  );
}
