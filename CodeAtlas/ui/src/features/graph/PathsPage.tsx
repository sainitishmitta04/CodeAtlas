import { useEffect, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useAction, useApi } from "../../hooks/useApi";
import { api } from "../../lib/api";
import { buildHash } from "../../lib/router";
import type { ImpactRow } from "../../lib/types";
import { PageHeader, Section, Panel, Metric } from "../../components/layout/Page";
import { Graph } from "../../components/graph/LazyGraph";
import { Badge, Collapsible, CypherBlock, EmptyState, ErrorState, Loading, Notice, Spinner } from "../../components/feedback/Feedback";

function RowsTable({ rows, kind }: { rows: ImpactRow[]; kind: "file" | "function" | "class" }) {
  if (!rows.length) return <p className="muted">None found.</p>;
  return (
    <table className="table">
      <thead>
        <tr>
          {kind !== "file" && <th>{kind === "function" ? "Function" : "Class"}</th>}
          <th>File</th>
          <th className="num">Hops</th>
          {kind !== "file" && <th>Confidence</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.file}:${r.function ?? r.class ?? ""}:${i}`}>
            {kind !== "file" && <td className="mono">{r.function ?? r.class}</td>}
            <td className="mono">{r.file}</td>
            <td className="num">{r.hops}</td>
            {kind !== "file" && (
              <td>{(r.confidences ?? []).map((c, j) => <Badge key={j} tone={c === "exact" ? "sage" : "warn"}>{c ?? "—"}</Badge>)}</td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PathsPage() {
  const { pid, route, focus, refreshKey } = useApp();
  const files = useApi((s) => api.files(pid!, s), [pid, refreshKey]);
  const list = files.data ?? [];
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [impactFile, setImpactFile] = useState("");
  const [useFocus, setUseFocus] = useState(false);
  const [depth, setDepth] = useState(2);
  const path = useAction((signal, f: string, t: string) => api.path({ mode: "dependency", from_id: f, to_id: t }, signal));
  const impact = useAction((signal, label: string, id: string, d: number) => api.impact({ label, node_id: id, depth: d }, signal));

  useEffect(() => {
    if (list.length >= 2) {
      setFrom((f) => f || list[0].id);
      setTo((t) => t || list[1].id);
    }
    if (list.length) setImpactFile((f) => f || list[0].id);
  }, [list]);

  // Deep link from the graph inspector: #/p/<pid>/paths?impact=<node id>&label=<label>
  const linked = route.params.impact && route.params.label ? { id: route.params.impact, label: route.params.label } : null;
  useEffect(() => {
    if (linked) void impact.run(linked.label, linked.id, depth);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linked?.id, linked?.label]);

  const focusUsable = focus && ["File", "Function", "Class", "Module"].includes(focus.label) && focus.props.project_id === pid;
  const fileName = (id: string) => list.find((f) => f.id === id)?.path ?? id;
  const imp = impact.data;

  return (
    <>
      <PageHeader eyebrow="Explore" title="Paths & change impact">
        Shortest static dependency chains between files, and what may be affected when a file, class, function or module changes.
      </PageHeader>

      <Section title="Dependency path" kicker="Shortest IMPORTS chain" index={1}>
        <Panel>
          {files.error ? <ErrorState error={files.error} onRetry={files.reload} /> : files.loading && !files.data ? <Loading label="Loading files…" rows={1} /> : list.length < 2 ? (
            <p className="muted">The project needs at least two files.</p>
          ) : (
            <form className="form-row" onSubmit={(e) => { e.preventDefault(); if (from !== to) void path.run(from, to); }}>
              <label className="field grow">
                <span className="field-label">From file</span>
                <select className="select" value={from} onChange={(e) => setFrom(e.target.value)}>
                  {list.map((f) => <option key={f.id} value={f.id}>{f.path}</option>)}
                </select>
              </label>
              <span className="form-row__arrow" aria-hidden="true">→</span>
              <label className="field grow">
                <span className="field-label">To file</span>
                <select className="select" value={to} onChange={(e) => setTo(e.target.value)}>
                  {list.map((f) => <option key={f.id} value={f.id}>{f.path}</option>)}
                </select>
              </label>
              <button className="btn btn--primary" type="submit" disabled={from === to || path.loading}>
                {path.loading ? <Spinner size={14} /> : null} Find path
              </button>
            </form>
          )}
          {from && from === to && <p className="hint">Choose two different files.</p>}
          {path.error != null && <ErrorState error={path.error} />}
          {path.data && (
            <div className="result-block">
              {path.data.note && <Notice tone={path.data.edges.length ? "info" : "warn"}>{path.data.note}</Notice>}
              {path.data.edges.length > 0 && (
                <>
                  <ol className="chain">
                    {path.data.nodes.map((n) => <li key={n.id} className="mono">{n.caption}</li>)}
                  </ol>
                  <Graph graph={path.data} height={360} compact ariaLabel={`Dependency path from ${fileName(from)} to ${fileName(to)}`} />
                </>
              )}
              <CypherBlock cypher={path.data.cypher} params={path.data.params} />
            </div>
          )}
        </Panel>
      </Section>

      <Section title="Change impact" kicker="Candidates for review, not confirmed breakages" index={2}>
        <Panel>
          <form
            className="form-row"
            onSubmit={(e) => {
              e.preventDefault();
              if (useFocus && focusUsable) void impact.run(focus.label, focus.id, depth);
              else if (impactFile) void impact.run("File", impactFile, depth);
            }}
          >
            <label className="field grow">
              <span className="field-label">Changed file</span>
              <select className="select" value={impactFile} disabled={useFocus && !!focusUsable} onChange={(e) => setImpactFile(e.target.value)}>
                {list.map((f) => <option key={f.id} value={f.id}>{f.path}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Depth</span>
              <select className="select" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                {[1, 2, 3].map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>
            <button className="btn btn--primary" type="submit" disabled={impact.loading || (!impactFile && !(useFocus && focusUsable))}>
              {impact.loading ? <Spinner size={14} /> : null} Analyze impact
            </button>
          </form>
          {focusUsable && (
            <label className="check">
              <input type="checkbox" checked={useFocus} onChange={(e) => setUseFocus(e.target.checked)} />
              Use the focused graph node instead ({focus.label} <span className="mono">{focus.caption}</span>)
            </label>
          )}
          {linked && <p className="hint">Opened from the graph for <span className="mono">{linked.label}</span> <span className="mono">{linked.id}</span>.</p>}
          {impact.error != null && <ErrorState error={impact.error} />}
          {impact.loading && !imp && <Loading label="Tracing dependents in Neo4j…" rows={2} />}
          {imp && (
            <div className="result-block">
              <div className="metric-row">
                <Metric label="Candidate affected files" value={imp.affected_files.length} accent={imp.affected_files.length ? "copper" : undefined} />
                <Metric label="Importing files" value={imp.importing_files.length} />
                <Metric label="Calling functions" value={imp.calling_functions.length} />
                <Metric label="Subclasses" value={imp.subclasses.length} />
              </div>
              <div className="grid grid--2">
                <div>
                  <h3 className="h3">Defined in the target</h3>
                  {imp.defined_symbols.length ? (
                    <ul className="plain-list mono">{imp.defined_symbols.map((s) => <li key={s}>{s}</li>)}</ul>
                  ) : <p className="muted">—</p>}
                  <h3 className="h3">Files importing it (file level)</h3>
                  <RowsTable rows={imp.importing_files} kind="file" />
                </div>
                <div>
                  <h3 className="h3">Functions calling into it (function level)</h3>
                  <RowsTable rows={imp.calling_functions} kind="function" />
                  <h3 className="h3">Subclasses</h3>
                  <RowsTable rows={imp.subclasses} kind="class" />
                </div>
              </div>
              <h3 className="h3">Candidate affected files</h3>
              {imp.affected_files.length ? (
                <div className="chips">{imp.affected_files.map((f) => <span key={f} className="chip mono">{f}</span>)}</div>
              ) : <p className="muted">None found.</p>}
              <Collapsible title="What this analysis cannot see" defaultOpen>
                <ul className="plain-list">{imp.coverage_notes.map((n) => <li key={n}>{n}</li>)}</ul>
              </Collapsible>
              {imp.graph.nodes.length > 0 ? (
                <Graph graph={imp.graph} height={420} compact ariaLabel="Change impact evidence graph" />
              ) : (
                <EmptyState title="No impact graph">The target has no recorded dependents.</EmptyState>
              )}
              <div className="row-links">
                <a className="link" href={buildHash("explain", pid, { node: imp.target.id })}>Explain the target →</a>
                {imp.target.label !== "Module" && (
                  <a className="link" href={buildHash("tours", pid, { kind: "impact", target: imp.target.id, start: "1" })}>Change-impact tour →</a>
                )}
              </div>
              <CypherBlock cypher={imp.graph.cypher} params={imp.graph.params} />
            </div>
          )}
        </Panel>
      </Section>
    </>
  );
}
