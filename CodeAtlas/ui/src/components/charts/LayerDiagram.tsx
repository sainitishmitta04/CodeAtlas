import { useMemo, useState } from "react";
import type { ArchitectureReport } from "../../lib/types";
import { layoutLayers } from "../../lib/layerLayout";
import { layerColor } from "../../lib/theme";

interface Props {
  arch: ArchitectureReport;
  selected: string | null;
  onSelect: (component: string) => void;
}

const CONF_OPACITY = { high: 1, medium: 0.78, low: 0.5, none: 0.28 } as const;

/** Layers as bands, components as boxes, observed component imports as curves. */
export function LayerDiagram({ arch, selected, onSelect }: Props) {
  const layout = useMemo(() => layoutLayers(arch), [arch]);
  const [hover, setHover] = useState<string | null>(null);
  const focus = hover ?? selected;
  const linked = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    for (const e of layout.edges) {
      if (e.source === focus) s.add(e.target);
      if (e.target === focus) s.add(e.source);
    }
    return s;
  }, [focus, layout.edges]);

  return (
    <figure className="layer-diagram">
      <div className="layer-diagram__scroll">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="group"
        aria-label={`Layer diagram: ${layout.nodes.length} components in ${layout.bands.length} layers, ${layout.edges.length} dependencies`}
      >
        <defs>
          <marker id="ld-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,1 L10,5 L0,9 z" fill="rgba(240,237,228,0.45)" />
          </marker>
          <marker id="ld-arrow-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,1 L10,5 L0,9 z" fill="#C58B65" />
          </marker>
          <marker id="ld-arrow-bad" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,1 L10,5 L0,9 z" fill="#D27A6A" />
          </marker>
        </defs>
        {layout.bands.map((b) => (
          <g key={b.layer} className="ld-band">
            <rect x={0} y={b.y} width={layout.width} height={b.h} rx={10} className="ld-band__bg" />
            <rect x={0} y={b.y} width={4} height={b.h} rx={2} fill={layerColor(b.layer)} />
            <text x={18} y={b.y + 24} className="ld-band__title">{b.title}</text>
            <text x={18} y={b.y + 42} className="ld-band__meta">{b.count} component{b.count === 1 ? "" : "s"}</text>
          </g>
        ))}
        <g className="ld-edges">
          {layout.edges.map((e) => {
            const hot = !!focus && (e.source === focus || e.target === focus);
            const dim = !!focus && !hot;
            return (
              <path
                key={e.key}
                d={e.d}
                className={`ld-edge${hot ? " is-hot" : ""}${dim ? " is-dim" : ""}${e.violation ? " is-violation" : ""}`}
                markerEnd={`url(#${e.violation ? "ld-arrow-bad" : hot ? "ld-arrow-hot" : "ld-arrow"})`}
                strokeWidth={Math.min(3.2, 0.8 + Math.log2(1 + e.imports) * 0.6)}
              >
                <title>{`${e.source} → ${e.target}: ${e.imports} import(s)${e.calls ? `, ${e.calls} call(s)` : ""}${e.violation ? " — possible layering violation" : ""}`}</title>
              </path>
            );
          })}
        </g>
        {layout.nodes.map((n) => {
          const isSel = n.name === selected;
          const dim = linked && !linked.has(n.name);
          return (
            <g
              key={n.name}
              className={`ld-node${isSel ? " is-selected" : ""}${dim ? " is-dim" : ""}${n.layer === "unclassified" ? " is-unclassified" : ""}`}
              transform={`translate(${n.x},${n.y})`}
              tabIndex={0}
              role="button"
              aria-pressed={isSel}
              aria-label={`${n.name}, ${n.layer}, ${n.confidence} confidence, ${n.files} file(s)`}
              onClick={() => onSelect(n.name)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(n.name);
                }
              }}
              onMouseEnter={() => setHover(n.name)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(n.name)}
              onBlur={() => setHover(null)}
            >
              <rect width={n.w} height={n.h} rx={8} className="ld-node__box" style={{ stroke: layerColor(n.layer), strokeOpacity: CONF_OPACITY[n.confidence] }} />
              <rect width={4} height={n.h - 12} x={8} y={6} rx={2} fill={layerColor(n.layer)} opacity={CONF_OPACITY[n.confidence]} />
              <text x={20} y={n.h / 2 + 4.5} className="ld-node__label">{n.name.length > 28 ? `${n.name.slice(0, 27)}…` : n.name}</text>
            </g>
          );
        })}
      </svg>
      </div>
      <figcaption className="ld-caption">
        Arrows point from a component to the component it imports. Box outline strength reflects classification confidence;
        <span className="ld-key ld-key--bad" /> marks a possible layering violation.
        {layout.hidden > 0 && ` Showing the ${layout.nodes.length} largest components; ${layout.hidden} more are listed below.`}
      </figcaption>
    </figure>
  );
}
