import { LABEL_COLORS, REL_COLORS } from "../../lib/theme";

export function GraphLegend({ labels, rels }: { labels?: string[]; rels?: string[] }) {
  return (
    <div className="legend" aria-label="Legend">
      {(labels ?? Object.keys(LABEL_COLORS)).map((l) => (
        <span key={l} className="legend__item">
          <i className="legend__dot" style={{ background: LABEL_COLORS[l] }} />
          {l}
        </span>
      ))}
      {rels && rels.length > 0 && <span className="legend__sep" aria-hidden="true" />}
      {rels?.map((r) => (
        <span key={r} className="legend__item legend__item--rel">
          <i className="legend__line" style={{ background: REL_COLORS[r] }} />
          {r}
        </span>
      ))}
    </div>
  );
}
