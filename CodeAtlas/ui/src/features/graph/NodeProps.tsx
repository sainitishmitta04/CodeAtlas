import type { Props } from "../../lib/types";

const HIDDEN = new Set(["project_id", "last_run_id"]);
const ORDER = ["id", "qualname", "name", "path", "file_path", "start_line", "end_line", "kind", "module", "docstring"];

function render(v: unknown): string {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.length ? v.map(String).join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Stored node properties, verbatim from Neo4j. */
export function NodeProps({ props }: { props: Props }) {
  const keys = Object.keys(props)
    .filter((k) => !HIDDEN.has(k))
    .sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b));
  return (
    <dl className="props">
      {keys.map((k) => (
        <div key={k} className={`props__row${k === "docstring" ? " props__row--long" : ""}`}>
          <dt>{k}</dt>
          <dd className={k === "docstring" ? "" : "mono"}>{render(props[k])}</dd>
        </div>
      ))}
    </dl>
  );
}
