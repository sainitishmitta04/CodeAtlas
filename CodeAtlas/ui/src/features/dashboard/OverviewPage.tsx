import type { CSSProperties } from "react";
import { useApp, useArchitecture } from "../../context/AppContext";
import { useApi } from "../../hooks/useApi";
import { api } from "../../lib/api";
import { buildHash } from "../../lib/router";
import { fmtNumber, fmtWhen, sum } from "../../lib/format";
import { LABEL_COLORS, REL_COLORS, layerColor } from "../../lib/theme";
import { PageHeader, Section, Panel, Metric } from "../../components/layout/Page";
import { BarList } from "../../components/charts/BarList";
import { Badge, ErrorState, Loading, Notice, EmptyState } from "../../components/feedback/Feedback";

export function OverviewPage() {
  const { pid, project, refreshKey } = useApp();
  const stats = useApi((s) => api.stats(pid!, s), [pid, refreshKey]);
  const detail = useApi((s) => api.project(pid!, s), [pid, refreshKey]);
  const arch = useArchitecture();
  if (!pid || !project) return null;

  const st = stats.data;
  const nodeTotal = st ? sum(Object.values(st.nodes)) : null;
  const relTotal = st ? sum(Object.values(st.relationships)) : null;
  const d: Record<string, unknown> = detail.data ?? {};
  const languages = Array.isArray(d.languages) ? (d.languages as string[]) : null;
  const sourceRef = typeof d.source_ref === "string" ? d.source_ref : null;

  return (
    <>
      <PageHeader
        eyebrow={<>Project · <span className="mono">{project.id}</span></>}
        title={project.name}
        actions={
          <>
            <a className="btn" href={buildHash("graph", pid)}>Open graph</a>
            <a className="btn btn--primary" href={buildHash("architecture", pid)}>Architecture</a>
          </>
        }
      >
        {project.source_kind ? `${project.source_kind === "github" ? "GitHub repository" : project.source_kind === "zip" ? "ZIP archive" : "Local directory"} · ` : ""}
        <span className="mono">{project.source_label ?? project.root_path ?? "—"}</span>
        {sourceRef ? <> · ref <span className="mono">{sourceRef}</span></> : null}
      </PageHeader>

      {project.sync_status && project.sync_status !== "complete" && (
        <Notice tone="warn" title={`Sync status: ${project.sync_status}`}>
          The graph may be partially updated ({project.sync_error || "interrupted"}). Re-run the analysis to repair it.
        </Notice>
      )}

      <div className="metric-row reveal" style={{ "--i": 1 } as CSSProperties}>
        <Metric label="Files" value={fmtNumber(st?.nodes.File ?? project.file_count)} hint={languages ? languages.join(", ") : undefined} />
        <Metric label="Graph nodes" value={fmtNumber(nodeTotal)} hint="all labels, this project" />
        <Metric label="Relationships" value={fmtNumber(relTotal)} hint="all types, this project" />
        <Metric
          label="Last sync"
          value={<span className="metric__small">{fmtWhen(project.synced_at)}</span>}
          hint={project.synced_at ?? undefined}
          accent={project.sync_status === "complete" ? "sage" : "warn"}
        />
      </div>

      {stats.error ? (
        <ErrorState error={stats.error} onRetry={stats.reload} />
      ) : !st ? (
        <Loading label="Reading project statistics from Neo4j…" />
      ) : (
        <div className="grid grid--2">
          <Section title="Node types" kicker="Composition" index={2}>
            <Panel>
              <BarList
                ariaLabel="Nodes by label"
                bars={Object.entries(st.nodes).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ key: k, label: k, value: v, color: LABEL_COLORS[k] }))}
              />
            </Panel>
          </Section>
          <Section title="Relationships" kicker="Structure" index={3}>
            <Panel>
              <BarList
                ariaLabel="Relationships by type"
                bars={Object.entries(st.relationships).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ key: k, label: k, value: v, color: REL_COLORS[k] }))}
              />
              <div className="kv-inline">
                {st.call_confidence.map((c) => (
                  <span key={String(c.confidence)}><Badge tone={c.confidence === "exact" ? "sage" : "warn"}>CALLS · {c.confidence ?? "—"}</Badge> {fmtNumber(c.edges)}</span>
                ))}
                {st.import_resolution.map((c) => (
                  <span key={String(c.resolution)}><Badge>IMPORTS · {c.resolution ?? "—"}</Badge> {fmtNumber(c.edges)}</span>
                ))}
              </div>
            </Panel>
          </Section>
        </div>
      )}

      <Section title="Architectural highlights" kicker="Inferred from the graph" index={4}
        actions={<a className="link" href={buildHash("architecture", pid)}>Full architecture →</a>}>
        {arch.error ? (
          <ErrorState error={arch.error} onRetry={arch.reload} />
        ) : !arch.data ? (
          <Loading label="Inferring layers from the project snapshot…" rows={2} />
        ) : !arch.data.components.length ? (
          <EmptyState title="No components to classify">The project has no analyzable source files.</EmptyState>
        ) : (
          <div className="grid grid--3">
            <Panel>
              <div className="panel__title">Layers</div>
              <ul className="layer-mini">
                {arch.data.layers.map((l) => (
                  <li key={l.layer}>
                    <i style={{ background: layerColor(l.layer) }} />
                    <span>{l.title}</span>
                    <span className="mono muted">{l.components.length} comp · {l.file_count} files</span>
                  </li>
                ))}
              </ul>
            </Panel>
            <Panel>
              <div className="panel__title">Entry points</div>
              {arch.data.entry_points.length ? (
                <ul className="link-list">
                  {arch.data.entry_points.slice(0, 8).map((e) => (
                    <li key={e}><a className="mono" href={buildHash("tours", pid, { kind: "entry", target: e, start: "1" })}>{e}</a></li>
                  ))}
                </ul>
              ) : <p className="muted">No entry points detected from observed evidence.</p>}
            </Panel>
            <Panel>
              <div className="panel__title">Signals</div>
              <dl className="kv">
                <dt>Components</dt><dd>{arch.data.components.length}</dd>
                <dt>Possible violations</dt><dd className={arch.data.violations.length ? "warn" : ""}>{arch.data.violations.length}</dd>
                <dt>Component cycles</dt><dd>{arch.data.component_cycles.length}</dd>
                <dt>File cycles</dt><dd>{arch.data.file_cycles.length}</dd>
                <dt>Unclassified</dt><dd>{arch.data.components.filter((c) => c.layer === "unclassified").length}</dd>
              </dl>
            </Panel>
          </div>
        )}
      </Section>

      {st && (
        <div className="grid grid--2">
          <Section title="Most imported files" kicker="Hubs" index={5}>
            <Panel>
              {st.top_imported_files.length ? (
                <table className="table">
                  <thead><tr><th>File</th><th className="num">Importers</th><th className="num">Symbols</th></tr></thead>
                  <tbody>
                    {st.top_imported_files.map((f) => (
                      <tr key={f.path}>
                        <td className="mono" title={f.docstring ?? undefined}>{f.path}</td>
                        <td className="num">{f.importers}</td>
                        <td className="num">{f.symbols}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="muted">No files.</p>}
            </Panel>
          </Section>
          <Section title="Most called functions" kicker="Hot paths (static)" index={6}>
            <Panel>
              {st.top_called_functions.length ? (
                <table className="table">
                  <thead><tr><th>Function</th><th className="num">Callers</th></tr></thead>
                  <tbody>
                    {st.top_called_functions.map((f) => (
                      <tr key={`${f.file}:${f.function}`}>
                        <td><span className="mono">{f.function}</span><div className="muted small mono">{f.file}</div></td>
                        <td className="num">{f.callers}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="muted">No resolved CALLS relationships.</p>}
            </Panel>
          </Section>
        </div>
      )}

      <Section title="Continue exploring" index={7}>
        <div className="shortcuts">
          {[
            { page: "graph" as const, title: "Graph", text: "Browse the complete project graph, page by page." },
            { page: "architecture" as const, title: "Architecture", text: "Layers, components, dependencies and possible violations." },
            { page: "tours" as const, title: "Guided tours", text: "Step through the codebase along observed relationships." },
            { page: "search" as const, title: "Search", text: "Lexical + graph search over identifiers, paths and docstrings." },
            { page: "ask" as const, title: "Q&A", text: "Ask questions answered from retrieved graph facts." },
          ].map((s, i) => (
            <a key={s.page} className="shortcut" href={buildHash(s.page, pid)} style={{ "--i": i } as CSSProperties}>
              <span className="shortcut__title">{s.title}</span>
              <span className="shortcut__text">{s.text}</span>
              <span className="shortcut__arrow" aria-hidden="true">→</span>
            </a>
          ))}
        </div>
      </Section>
    </>
  );
}
