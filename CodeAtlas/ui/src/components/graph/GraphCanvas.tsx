// WebGL graph renderer (Sigma.js + graphology). It draws exactly the nodes and relationships
// of the payload it is given; positions come from a ForceAtlas2 layout computed in the browser.

import { useEffect, useMemo, useRef, useState } from "react";
import Sigma from "sigma";
import type { Settings } from "sigma/settings";
import type { NodeDisplayData, PartialButFor } from "sigma/types";
import { MultiDirectedGraph } from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import type { GNode, GraphPayload } from "../../lib/types";
import { drawableEdges } from "../../lib/graph";
import { COLORS, REL_COLORS, labelColor } from "../../lib/theme";

interface Props {
  graph: GraphPayload;
  selectedId?: string | null;
  onSelect?: (node: GNode | null) => void;
  /** Node ids to emphasise (e.g. graph search matches). */
  highlight?: Set<string> | null;
  /** When this changes to a node id, the camera glides to that node. */
  focusId?: string | null;
  height?: number | string;
  /** Short accessible description of what the graph shows. */
  ariaLabel: string;
  compact?: boolean;
}

type NodeAttrs = { x: number; y: number; size: number; color: string; label: string; nlabel: string; caption: string };
type EdgeAttrs = { size: number; color: string; type: string; rel: string };

const DIM_NODE = "#2a302b";
const DIM_EDGE = "#1a1e1b";
const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function hexA(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, (c) => c + c) : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** Pre-blend a colour over the canvas background so edges stay subtle without alpha. */
function blend(hex: string, alpha: number, bg = "#121513"): string {
  const p = (h: string) => parseInt(h.replace("#", ""), 16);
  const a = p(hex), b = p(bg);
  const ch = (s: number) => Math.round(((a >> s) & 255) * alpha + ((b >> s) & 255) * (1 - alpha));
  return `#${((ch(16) << 16) | (ch(8) << 8) | ch(0)).toString(16).padStart(6, "0")}`;
}

type LabelData = PartialButFor<NodeDisplayData, "x" | "y" | "size" | "label" | "color">;

function drawLabel(ctx: CanvasRenderingContext2D, data: LabelData, settings: Settings<NodeAttrs, EdgeAttrs>): void {
  if (!data.label) return;
  const size = settings.labelSize;
  ctx.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`;
  const x = data.x + data.size + 4;
  const y = data.y + size / 3;
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(16,19,17,0.85)";
  ctx.strokeText(data.label, x, y);
  ctx.fillStyle = data.highlighted ? COLORS.ivory : "rgba(240,237,228,0.78)";
  ctx.fillText(data.label, x, y);
}

function drawHover(ctx: CanvasRenderingContext2D, data: LabelData, settings: Settings<NodeAttrs, EdgeAttrs>): void {
  const size = settings.labelSize + 1;
  ctx.font = `600 ${size}px ${settings.labelFont}`;
  const label = data.label ?? "";
  const w = ctx.measureText(label).width;
  const padX = 8;
  const h = size + 10;
  const x = data.x + data.size + 6;
  const y = data.y - h / 2;
  ctx.beginPath();
  ctx.arc(data.x, data.y, data.size + 4, 0, Math.PI * 2);
  ctx.fillStyle = hexA(data.color, 0.22);
  ctx.fill();
  if (!label) return;
  ctx.beginPath();
  ctx.roundRect(x, y, w + padX * 2, h, 6);
  ctx.fillStyle = "rgba(32,37,33,0.96)";
  ctx.fill();
  ctx.strokeStyle = hexA(data.color, 0.7);
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = COLORS.ivory;
  ctx.fillText(label, x + padX, data.y + size / 3);
}

export default function GraphCanvas({ graph, selectedId, onSelect, highlight, focusId, height = 620, ariaLabel, compact }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma<NodeAttrs, EdgeAttrs> | null>(null);
  const graphRef = useRef<MultiDirectedGraph<NodeAttrs, EdgeAttrs> | null>(null);
  const layoutRef = useRef<FA2Layout | null>(null);
  const layoutTimer = useRef<number | null>(null);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const nodesById = useRef(new Map<string, GNode>());
  const stateRef = useRef<{ hovered: string | null; selected: string | null; highlight: Set<string> | null }>({ hovered: null, selected: null, highlight: null });
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [tip, setTip] = useState<{ x: number; y: number; node: GNode } | null>(null);
  const [layoutRunning, setLayoutRunning] = useState(false);
  const [webglError, setWebglError] = useState<string | null>(null);

  const edges = useMemo(() => drawableEdges(graph), [graph]);

  const stopLayout = () => {
    if (layoutTimer.current) window.clearTimeout(layoutTimer.current);
    layoutTimer.current = null;
    layoutRef.current?.kill();
    layoutRef.current = null;
    setLayoutRunning(false);
  };

  const runLayout = (g: MultiDirectedGraph<NodeAttrs, EdgeAttrs>, fresh: boolean) => {
    stopLayout();
    if (g.order < 2) return;
    const settings = { ...forceAtlas2.inferSettings(g), barnesHutOptimize: g.order > 250, adjustSizes: false, slowDown: 6 };
    if (prefersReducedMotion() || g.order > 6000) {
      forceAtlas2.assign(g, { iterations: g.order > 2000 ? 40 : fresh ? 160 : 60, settings });
      sigmaRef.current?.refresh();
      return;
    }
    // Settle most of the structure synchronously, then let the worker finish visibly.
    if (fresh) forceAtlas2.assign(g, { iterations: g.order > 1500 ? 20 : 60, settings });
    const layout = new FA2Layout(g, { settings });
    layoutRef.current = layout;
    layout.start();
    setLayoutRunning(true);
    layoutTimer.current = window.setTimeout(stopLayout, Math.min(1400 + g.order * 2, 6000));
  };

  // Create the renderer once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const g = new MultiDirectedGraph<NodeAttrs, EdgeAttrs>();
    graphRef.current = g;
    let renderer: Sigma<NodeAttrs, EdgeAttrs>;
    try {
      renderer = new Sigma<NodeAttrs, EdgeAttrs>(g, container, {
        defaultEdgeType: "arrow",
        renderEdgeLabels: false,
        labelFont: "Geist Variable, Geist, Inter, system-ui, sans-serif",
        labelSize: 13,
        labelWeight: "500",
        labelColor: { color: COLORS.ivory },
        labelDensity: 0.35,
        labelGridCellSize: 90,
        labelRenderedSizeThreshold: compact ? 5 : 8,
        defaultDrawNodeLabel: drawLabel,
        defaultDrawNodeHover: drawHover,
        zIndex: true,
        minCameraRatio: 0.03,
        maxCameraRatio: 8,
        stagePadding: 40,
        hideEdgesOnMove: false,
        nodeReducer: (node, data) => {
          const { hovered, selected, highlight: hl } = stateRef.current;
          const focus = hovered ?? selected;
          const res: Partial<NodeDisplayData> = { ...data };
          if (hl && hl.size) {
            if (hl.has(node)) {
              res.highlighted = true;
              res.forceLabel = true;
              res.zIndex = 2;
            } else if (!focus) {
              res.color = DIM_NODE;
              res.label = "";
            }
          }
          if (focus) {
            if (node === focus) {
              res.highlighted = true;
              res.forceLabel = true;
              res.size = data.size * 1.35;
              res.zIndex = 3;
            } else if (g.hasNode(focus) && g.areNeighbors(node, focus)) {
              res.forceLabel = !compact && g.degree(focus) <= 24;
              res.zIndex = 2;
            } else {
              res.color = DIM_NODE;
              res.label = "";
              res.zIndex = 0;
            }
          }
          if (node === selected) res.highlighted = true;
          return res;
        },
        edgeReducer: (edge, data) => {
          const { hovered, selected } = stateRef.current;
          const focus = hovered ?? selected;
          if (!focus) return data;
          const [s, t] = g.extremities(edge);
          if (s === focus || t === focus) {
            return { ...data, color: s === focus ? COLORS.copper : COLORS.sage, size: Math.max(data.size, 1.6), zIndex: 2 };
          }
          return { ...data, color: DIM_EDGE, zIndex: 0 };
        },
      });
    } catch (err) {
      setWebglError(err instanceof Error ? err.message : String(err));
      return;
    }
    sigmaRef.current = renderer;

    const showTip = (node: string) => {
      const n = nodesById.current.get(node);
      if (!n) return;
      const pos = renderer.graphToViewport({ x: g.getNodeAttribute(node, "x"), y: g.getNodeAttribute(node, "y") });
      setTip({ x: pos.x, y: pos.y, node: n });
    };
    renderer.on("enterNode", ({ node }) => {
      stateRef.current.hovered = node;
      container.style.cursor = "pointer";
      showTip(node);
      renderer.refresh({ skipIndexation: true });
    });
    renderer.on("leaveNode", () => {
      stateRef.current.hovered = null;
      container.style.cursor = "";
      setTip(null);
      renderer.refresh({ skipIndexation: true });
    });
    renderer.on("clickNode", ({ node }) => {
      onSelectRef.current?.(nodesById.current.get(node) ?? null);
    });
    renderer.on("clickStage", () => onSelectRef.current?.(null));

    // Drag nodes.
    let dragged: string | null = null;
    renderer.on("downNode", ({ node }) => {
      dragged = node;
      stopLayout();
      if (!renderer.getCustomBBox()) renderer.setCustomBBox(renderer.getBBox());
    });
    const captor = renderer.getMouseCaptor();
    captor.on("mousemovebody", (e) => {
      if (!dragged) return;
      const pos = renderer.viewportToGraph(e);
      g.setNodeAttribute(dragged, "x", pos.x);
      g.setNodeAttribute(dragged, "y", pos.y);
      positions.current.set(dragged, pos);
      setTip(null);
      e.preventSigmaDefault();
      e.original.preventDefault();
      e.original.stopPropagation();
    });
    const endDrag = () => {
      dragged = null;
    };
    captor.on("mouseup", endDrag);
    renderer.getCamera().on("updated", () => setTip(null));

    return () => {
      stopLayout();
      renderer.kill();
      sigmaRef.current = null;
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync the graphology graph with the payload, keeping known positions.
  useEffect(() => {
    const g = graphRef.current;
    const renderer = sigmaRef.current;
    if (!g || !renderer) return;
    // Remember positions from the current layout.
    g.forEachNode((id, a) => positions.current.set(id, { x: a.x, y: a.y }));
    const wanted = new Set(graph.nodes.map((n) => n.id));
    const hadNodes = g.order;
    g.forEachNode((id) => {
      if (!wanted.has(id)) g.dropNode(id);
    });
    nodesById.current = new Map(graph.nodes.map((n) => [n.id, n]));
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    let added = 0;
    const neighbourOf = new Map<string, string>();
    for (const e of edges) {
      neighbourOf.set(e.target, e.source);
      if (!neighbourOf.has(e.source)) neighbourOf.set(e.source, e.target);
    }
    graph.nodes.forEach((n, i) => {
      const d = degree.get(n.id) ?? 0;
      const size = (n.label === "Project" ? 9 : n.label === "Module" ? 2.6 : 3.2) + Math.min(9, Math.log2(1 + d) * 1.6);
      const attrs = { size, color: labelColor(n.label), label: n.caption, nlabel: n.label, caption: n.caption };
      if (g.hasNode(n.id)) {
        g.mergeNodeAttributes(n.id, attrs);
        return;
      }
      let pos = positions.current.get(n.id);
      if (!pos) {
        const anchor = neighbourOf.get(n.id);
        const ap = anchor ? positions.current.get(anchor) ?? (g.hasNode(anchor) ? { x: g.getNodeAttribute(anchor, "x"), y: g.getNodeAttribute(anchor, "y") } : null) : null;
        const angle = (i * 2.399963) % (Math.PI * 2); // golden-angle spiral
        const r = ap ? 2 + Math.random() * 3 : 10 * Math.sqrt(i + 1);
        pos = { x: (ap?.x ?? 0) + Math.cos(angle) * r, y: (ap?.y ?? 0) + Math.sin(angle) * r };
      }
      g.addNode(n.id, { ...attrs, ...pos });
      added++;
    });
    const wantedEdges = new Set(edges.map((e) => e.id));
    g.forEachEdge((id) => {
      if (!wantedEdges.has(id)) g.dropEdge(id);
    });
    for (const e of edges) {
      if (g.hasEdge(e.id)) continue;
      const color = blend(REL_COLORS[e.type] ?? COLORS.muted, e.type === "CONTAINS" ? 0.45 : 0.7);
      g.addDirectedEdgeWithKey(e.id, e.source, e.target, { size: e.type === "CONTAINS" ? 0.6 : 1, color, type: "arrow", rel: e.type });
    }
    if (added > 0) {
      renderer.setCustomBBox(null);
      runLayout(g, hadNodes === 0 || added > hadNodes);
    }
    renderer.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, edges]);

  // Selection / highlight changes only need a re-render.
  useEffect(() => {
    stateRef.current.selected = selectedId ?? null;
    stateRef.current.highlight = highlight ?? null;
    sigmaRef.current?.refresh({ skipIndexation: true });
  }, [selectedId, highlight]);

  // Camera glide to a node.
  useEffect(() => {
    const renderer = sigmaRef.current;
    const g = graphRef.current;
    if (!renderer || !g || !focusId || !g.hasNode(focusId)) return;
    const d = renderer.getNodeDisplayData(focusId);
    if (!d) return;
    // Offset to the left of centre so the node is not hidden behind the inspector overlay.
    const ratio = 0.3;
    renderer.getCamera().animate({ x: d.x + (compact ? 0 : ratio * 0.22), y: d.y, ratio }, { duration: prefersReducedMotion() ? 0 : 600 });
  }, [focusId]);

  const camera = (fn: "in" | "out" | "reset") => {
    const cam = sigmaRef.current?.getCamera();
    if (!cam) return;
    const duration = prefersReducedMotion() ? 0 : 300;
    if (fn === "in") cam.animatedZoom({ duration });
    else if (fn === "out") cam.animatedUnzoom({ duration });
    else cam.animatedReset({ duration });
  };

  if (webglError) {
    return (
      <div className="graph-canvas graph-canvas--error" style={{ height }} role="img" aria-label={ariaLabel}>
        <p>The graph could not be drawn: WebGL is unavailable in this browser ({webglError}).</p>
      </div>
    );
  }

  return (
    <div className={`graph-canvas${compact ? " graph-canvas--compact" : ""}`} style={{ height }}>
      <div ref={containerRef} className="graph-canvas__stage" role="img" aria-label={ariaLabel} />
      <div className="graph-canvas__vignette" aria-hidden="true" />
      <div className="graph-canvas__tools" role="toolbar" aria-label="Graph view controls">
        <button className="icon-btn" onClick={() => camera("in")} aria-label="Zoom in" title="Zoom in">+</button>
        <button className="icon-btn" onClick={() => camera("out")} aria-label="Zoom out" title="Zoom out">−</button>
        <button className="icon-btn" onClick={() => camera("reset")} aria-label="Fit graph to view" title="Fit to view">⤢</button>
        <button
          className={`icon-btn${layoutRunning ? " is-active" : ""}`}
          onClick={() => (layoutRunning ? stopLayout() : graphRef.current && runLayout(graphRef.current, false))}
          aria-label={layoutRunning ? "Stop layout" : "Re-run layout"}
          title={layoutRunning ? "Stop layout" : "Re-run layout"}
        >
          {layoutRunning ? "❚❚" : "↻"}
        </button>
      </div>
      {tip && (
        <div className="graph-tip" style={{ transform: `translate(${tip.x + 14}px, ${tip.y + 12}px)` }} role="tooltip">
          <span className="graph-tip__label" style={{ color: labelColor(tip.node.label) }}>{tip.node.label}</span>
          <span className="graph-tip__name mono">{tip.node.caption}</span>
          {typeof tip.node.props.file_path === "string" && tip.node.label !== "File" && (
            <span className="graph-tip__meta mono">
              {tip.node.props.file_path}
              {tip.node.props.start_line != null ? `:${String(tip.node.props.start_line)}` : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
