import { useMemo, useState, type CSSProperties } from "react";
import { useApp, useArchitecture } from "../../context/AppContext";
import { buildHash, navigate } from "../../lib/router";
import { fmtNumber } from "../../lib/format";
import { layerColor } from "../../lib/theme";
import type { ArchitectureReport, Component } from "../../lib/types";
import { PageHeader, Section, Panel, Metric } from "../../components/layout/Page";
import { LayerDiagram } from "../../components/charts/LayerDiagram";
import { Badge, Collapsible, ConfidenceMeter, EmptyState, ErrorState, Loading, Notice } from "../../components/feedback/Feedback";

const FILE_PAGE = 100;

function DepList({ title, deps, onPick }: { title: string; deps: Component["depends_on"]; onPick: (c: string) => void }) {
  return (
    <div>
      <div className="panel__title">{title}</div>
      {deps.length ? (
        <ul className="dep-list">
          {deps.map((d) => (
            <li key={d.component}>
              <button className="link-btn mono" onClick={() => onPick(d.component)}>{d.component}</button>
              <span className="muted small">{d.imports} import{d.imports === 1 ? "" : "s"}{d.calls ? ` · ${d.calls} call${d.calls === 1 ? "" : "s"}` : ""}</span>
            </li>
          ))}
        </ul>
      ) : <p className="muted small">—</p>}
    </div>
  );
}

function Inspector({ arch, comp, onPick }: { arch: ArchitectureReport; comp: Component; onPick: (c: string) => void }) {
  const { pid } = useApp();
  const fileIds = useMemo(() => new Map(arch.files.map((f) => [f.path, f.file_id])), [arch.files]);
  const fileRows = arch.files.filter((f) => f.component === comp.name);
  const title = arch.layers.find((l) => l.layer === comp.layer)?.title ?? comp.layer;
  return (
    <Panel tone="paper" className="comp-inspector" key={comp.name}>
      <div className="paper__eyebrow">
        <i style={{ background: layerColor(comp.layer) }} /> {title} · inferred
      </div>
      <h3 className="paper__title mono">{comp.name}</h3>
      <div className="paper__meta">
        <ConfidenceMeter value={comp.confidence} />
        <span>{comp.files.length} file{comp.files.length === 1 ? "" : "s"} · {fmtNumber(comp.symbol_count)} symbols · {fmtNumber(comp.loc)} lines</span>
      </div>
      {comp.ambiguous_between.length > 0 && (
        <p className="paper__warn">Ambiguous between {comp.ambiguous_between.join(", ")} — evidence does not clearly favour one layer.</p>
      )}
      <div className="paper__section">Observed evidence</div>
      {comp.evidence.length ? (
        <ul className="evidence">{comp.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
      ) : <p className="ink-muted">No layer evidence was observed for this component.</p>}
      {Object.keys(comp.scores).length > 0 && (
        <div className="scores">
          {Object.entries(comp.scores).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([l, s]) => (
            <span key={l} className="score"><i style={{ background: layerColor(l) }} />{l} <b className="mono">{s.toFixed(1)}</b></span>
          ))}
        </div>
      )}
      <div className="paper__cols">
        <DepList title="Depends on" deps={comp.depends_on} onPick={onPick} />
        <DepList title="Used by" deps={comp.used_by} onPick={onPick} />
      </div>
      {comp.entry_points.length > 0 && (
        <>
          <div className="paper__section">Entry points</div>
          <ul className="plain-list mono">{comp.entry_points.map((e) => <li key={e}>{e}</li>)}</ul>
        </>
      )}
      <div className="paper__section">Files</div>
      <ul className="file-list">
        {(fileRows.length ? fileRows.map((f) => f.path) : comp.files).slice(0, 40).map((p) => {
          const id = fileIds.get(p);
          const fl = fileRows.find((f) => f.path === p);
          return (
            <li key={p}>
              {id ? <a className="mono" href={buildHash("explain", pid, { node: id })}>{p}</a> : <span className="mono">{p}</span>}
              {fl && <ConfidenceMeter value={fl.confidence} showLabel={false} />}
            </li>
          );
        })}
      </ul>
      {comp.files.length > 40 && <p className="ink-muted small">…and {comp.files.length - 40} more (see the per-file table).</p>}
      <div className="paper__actions">
        <button className="btn btn--ink" onClick={() => navigate("tours", pid, { kind: "component", target: comp.name, start: "1" })}>Component tour</button>
        <button className="btn btn--ink-ghost" onClick={() => navigate("tours", pid, { kind: "dependency", target: comp.name, start: "1" })}>Dependency tour</button>
        <button className="btn btn--ink-ghost" onClick={() => navigate("tours", pid, { kind: "impact", target: comp.name, start: "1" })}>Impact tour</button>
      </div>
    </Panel>
  );
}

export function ArchitecturePage() {
  const { pid, route } = useApp();
  const arch = useArchitecture();
  const [picked, setPicked] = useState<string | null>(route.params.component ?? null);
  const [fileFilter, setFileFilter] = useState("");
  const [fileLimit, setFileLimit] = useState(FILE_PAGE);

  const a = arch.data;
  const comp = a?.components.find((c) => c.name === picked) ?? null;
  const pick = (name: string) => {
    setPicked(name);
    navigate("architecture", pid, { component: name }, true);
    requestAnimationFrame(() => document.getElementById("component-inspector")?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" }));
  };

  const files = useMemo(() => {
    if (!a) return [];
    const q = fileFilter.trim().toLowerCase();
    return q ? a.files.filter((f) => f.path.toLowerCase().includes(q) || f.component.toLowerCase().includes(q) || f.layer.includes(q)) : a.files;
  }, [a, fileFilter]);

  return (
    <>
      <PageHeader eyebrow="Intelligence" title="Architecture">
        Layers are <em>inferred</em> from observed evidence — framework imports, decorators, base classes, names and import structure.
        Dependencies are observed imports between components.
      </PageHeader>

      {arch.error ? (
        <ErrorState error={arch.error} onRetry={arch.reload} />
      ) : !a ? (
        <Loading label="Inferring layers from the project snapshot…" rows={4} />
      ) : !a.components.length ? (
        <EmptyState title="Nothing to classify">This project has no analyzable source files.</EmptyState>
      ) : (
        <>
          <div className="metric-row reveal" style={{ "--i": 1 } as CSSProperties}>
            <Metric label="Components" value={a.components.length} hint={`${a.files.length} files classified`} />
            <Metric label="Layers in use" value={a.layers.length} />
            <Metric label="Possible violations" value={a.violations.length} accent={a.violations.length ? "warn" : "sage"} />
            <Metric label="Cycles" value={a.component_cycles.length + a.file_cycles.length} hint={`${a.component_cycles.length} component · ${a.file_cycles.length} file`} />
            <Metric label="Entry points" value={a.entry_points.length} />
          </div>

          <Section title="Layer overview" kicker="Components per inferred layer" index={2}>
            <div className="layer-strip">
              {a.layers.map((l, i) => (
                <div key={l.layer} className="layer-card" style={{ "--i": i, "--c": layerColor(l.layer) } as CSSProperties}>
                  <div className="layer-card__title">{l.title}</div>
                  <div className="layer-card__num">{l.components.length}</div>
                  <div className="layer-card__meta">components · {l.file_count} files</div>
                </div>
              ))}
            </div>
          </Section>

          <Section title="Layer dependencies" kicker="Observed component imports" index={3}>
            <Panel className="diagram-panel">
              <LayerDiagram arch={a} selected={picked} onSelect={pick} />
            </Panel>
            {a.notes.map((n) => <Notice key={n} tone="info">{n}</Notice>)}
          </Section>

          <div className="arch-split">
            <div className="arch-split__main">
              <Section title="Components" kicker="Grouped by inferred layer" index={4}>
                {a.layers.map((l) => (
                  <div key={l.layer} className="comp-group">
                    <div className="comp-group__head"><i style={{ background: layerColor(l.layer) }} />{l.title}<span className="muted small">{l.components.length}</span></div>
                    <div className="comp-chips">
                      {l.components.map((name) => {
                        const c = a.components.find((x) => x.name === name);
                        if (!c) return null;
                        return (
                          <button key={name} className={`comp-chip${picked === name ? " is-selected" : ""}`} onClick={() => pick(name)} aria-pressed={picked === name}>
                            <span className="mono">{name}</span>
                            <ConfidenceMeter value={c.confidence} showLabel={false} />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </Section>

              <Section title="Possible layering violations" kicker="Heuristic — review before acting" index={5}>
                {a.violations.length ? (
                  <ul className="violations">
                    {a.violations.map((v) => (
                      <li key={`${v.source_component}-${v.target_component}`}>
                        <div>
                          <button className="link-btn mono" onClick={() => pick(v.source_component)}>{v.source_component}</button>
                          <Badge>{v.source_layer}</Badge>
                          <span className="arrow" aria-label="imports">→</span>
                          <button className="link-btn mono" onClick={() => pick(v.target_component)}>{v.target_component}</button>
                          <Badge>{v.target_layer}</Badge>
                        </div>
                        <div className="muted small">{v.imports} import{v.imports === 1 ? "" : "s"} · e.g. <span className="mono">{v.example}</span></div>
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted">No possible violations between confidently classified components.</p>}
              </Section>

              <Section title="Cycles" kicker="Tightly coupled groups" index={6}>
                {a.component_cycles.length || a.file_cycles.length ? (
                  <ul className="cycles">
                    {a.component_cycles.map((c, i) => (
                      <li key={`c${i}`}><Badge tone="copper">components</Badge> {c.map((n, j) => (
                        <span key={n}>{j > 0 && <span className="arrow"> ↔ </span>}<button className="link-btn mono" onClick={() => pick(n)}>{n}</button></span>
                      ))}</li>
                    ))}
                    {a.file_cycles.map((c, i) => (
                      <li key={`f${i}`}><Badge>files</Badge> <span className="mono">{c.join(" ↔ ")}</span></li>
                    ))}
                  </ul>
                ) : <p className="muted">No import cycles observed.</p>}
              </Section>

              <Section title="Entry points" kicker="Where execution plausibly begins" index={7}>
                {a.entry_points.length ? (
                  <ul className="link-list">
                    {a.entry_points.map((e) => (
                      <li key={e}>
                        <span className="mono">{e}</span>
                        <a className="link" href={buildHash("tours", pid, { kind: "entry", target: e, start: "1" })}>Entry-point tour →</a>
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted">No entry points detected from observed evidence.</p>}
              </Section>
            </div>

            <aside className="arch-split__side" id="component-inspector" aria-label="Component inspector">
              {comp ? <Inspector arch={a} comp={comp} onPick={pick} /> : (
                <Panel tone="flat" className="inspector__empty">
                  <p>Select a component in the diagram or the list to inspect its files, dependencies and the evidence behind its layer.</p>
                </Panel>
              )}
            </aside>
          </div>

          <Section title="Per-file classification" kicker={`${a.files.length} files`} index={8}>
            <Collapsible title="File table with evidence" meta={<span>{files.length} shown</span>}>
              <input className="input" type="search" placeholder="Filter by path, component or layer" value={fileFilter} onChange={(e) => { setFileFilter(e.target.value); setFileLimit(FILE_PAGE); }} aria-label="Filter files" />
              <div className="table-scroll">
                <table className="table">
                  <thead><tr><th>File</th><th>Component</th><th>Layer</th><th>Confidence</th><th>Evidence</th></tr></thead>
                  <tbody>
                    {files.slice(0, fileLimit).map((f) => (
                      <tr key={f.file_id}>
                        <td className="mono"><a href={buildHash("explain", pid, { node: f.file_id })}>{f.path}</a></td>
                        <td className="mono">{f.component}</td>
                        <td><span className="layer-tag"><i style={{ background: layerColor(f.layer) }} />{f.layer}</span></td>
                        <td><ConfidenceMeter value={f.confidence} /></td>
                        <td className="small">{f.evidence.slice(0, 4).map((e) => e.detail).join("; ") || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {files.length > fileLimit && <button className="btn btn--ghost" onClick={() => setFileLimit((l) => l + FILE_PAGE)}>Show {Math.min(FILE_PAGE, files.length - fileLimit)} more</button>}
            </Collapsible>
          </Section>
        </>
      )}
    </>
  );
}
