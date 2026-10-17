import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../../context/AppContext";
import { api } from "../../lib/api";
import { ApiError, isAbort } from "../../lib/errors";
import { buildHash } from "../../lib/router";
import { fmtBytes, fmtNumber, sum } from "../../lib/format";
import type { AnalysisReport, SourceTarget, WriteGuard } from "../../lib/types";
import { PageHeader, Panel, Metric, Section } from "../../components/layout/Page";
import { ProgressFlow, type FlowStep } from "../../components/flow/ProgressFlow";
import { Badge, Collapsible, ErrorState, Notice, Spinner } from "../../components/feedback/Feedback";

type Kind = "local" | "github" | "zip";
const KINDS: { kind: Kind; label: string }[] = [
  { kind: "local", label: "Local directory" },
  { kind: "github", label: "GitHub URL" },
  { kind: "zip", label: "ZIP upload" },
];

/** SHA-256 of the upload so a confirmation is tied to the exact bytes it was shown for. */
async function digest(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function TargetCard({ t }: { t: SourceTarget }) {
  const state = t.exists ? "existing project — will be updated" : t.exists === false ? "new project" : "existence unknown";
  return (
    <div className="target-card">
      <div className="target-card__row">
        <span className="field-label">Target</span>
        <span className="mono">{t.project_id}</span>
        <Badge tone={t.exists ? "warn" : t.exists === false ? "sage" : "neutral"}>{state}</Badge>
      </div>
      <div className="target-card__row"><span className="field-label">Name</span><b>{t.project_name}</b></div>
      <div className="target-card__row"><span className="field-label">Source</span><span className="mono">{t.label}</span></div>
      {t.ref && <div className="target-card__row"><span className="field-label">Ref</span><span className="mono">{t.ref}</span>{t.ref_is_default && <span className="muted small">(default branch)</span>}</div>}
      {t.exists && (
        <div className="target-card__row muted small">
          Currently: {t.existing_name} · status {t.existing_status} · synced {t.existing_synced_at ?? "—"}
        </div>
      )}
      {t.notes.map((n) => <p key={n} className="hint">{n}</p>)}
    </div>
  );
}

function Report({ rep }: { rep: AnalysisReport }) {
  const src = rep.source;
  const nodes = sum(Object.values(rep.node_counts));
  const rels = sum(Object.values(rep.rel_counts));
  return (
    <Section title={rep.dry_run ? `Dry run (nothing written): ${rep.project_name}` : `Synced to Neo4j Aura: ${rep.project_name}`} kicker={rep.dry_run ? "Dry run" : `Status ${rep.sync_status ?? "—"}`}>
      <Panel>
        <p className="muted">
          Project <span className="mono">{rep.project_id}</span> · {src?.kind ?? "local"} · <span className="mono">{src?.label ?? rep.root_path}</span>
          {src?.ref && <> · ref <span className="mono">{src.ref}</span>{src.ref_is_default ? " (default branch)" : ""}</>}
          {src?.exists != null && <> · {rep.dry_run ? (src.exists ? "would update an existing project" : "new project") : src.exists ? "updated an existing project" : "created a new project"}</>}
        </p>
        <div className="metric-row">
          <Metric label="Files analyzed" value={fmtNumber(rep.files_analyzed)} />
          <Metric label="Nodes" value={fmtNumber(nodes)} />
          <Metric label="Relationships" value={fmtNumber(rels)} />
          <Metric label="Skipped files" value={fmtNumber(rep.skipped.length)} accent={rep.skipped.length ? "warn" : undefined} />
        </div>
        <div className="grid grid--3">
          {[["Nodes", rep.node_counts], ["Relationships", rep.rel_counts], ["Resolution", rep.resolution]].map(([title, obj]) => (
            <div key={title as string}>
              <div className="panel__title">{title as string}</div>
              <dl className="kv">
                {Object.entries(obj as Record<string, number>).map(([k, v]) => (
                  <div key={k} className="kv__row"><dt>{k}</dt><dd className="mono">{fmtNumber(v)}</dd></div>
                ))}
              </dl>
            </div>
          ))}
        </div>
        {!rep.dry_run && (
          <p className="hint">Run <span className="mono">{rep.run_id}</span> · removed stale: {rep.swept_nodes} nodes, {rep.swept_rels} relationships · {rep.duration_s} s</p>
        )}
        {src?.notes.map((n) => <Notice key={n} tone="info">{n}</Notice>)}
        {rep.files_with_syntax_errors.length > 0 && (
          <Notice tone="warn" title="Files with syntax errors (partially parsed)">{rep.files_with_syntax_errors.join(", ")}</Notice>
        )}
        {rep.skipped.length > 0 && (
          <Collapsible title={`Skipped files (${rep.skipped.length})`}>
            <table className="table"><tbody>{rep.skipped.map((s) => <tr key={s.path}><td className="mono">{s.path}</td><td>{s.reason}</td></tr>)}</tbody></table>
          </Collapsible>
        )}
        <Collapsible title="Diagnostics (raw API response)">
          <pre className="code code--block"><code>{JSON.stringify(rep, null, 2)}</code></pre>
        </Collapsible>
        {!rep.dry_run && <a className="btn btn--primary" href={buildHash("overview", rep.project_id)}>Open {rep.project_name}</a>}
      </Panel>
    </Section>
  );
}

export function AnalyzePage() {
  const { projects, health, reloadProjects, refresh } = useApp();
  const [kind, setKind] = useState<Kind>("local");
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileHash, setFileHash] = useState("");
  const [zipMode, setZipMode] = useState<"new" | "update">("new");
  const [zipTarget, setZipTarget] = useState("");
  const [pending, setPending] = useState<{ key: string; target: SourceTarget } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [running, setRunning] = useState<null | "dry" | "write">(null);
  const [error, setError] = useState<unknown>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  useEffect(() => () => ctrl.current?.abort(), []);

  const maxUpload = health?.max_upload_bytes ?? null;
  const zipProjects = (projects ?? []).filter((p) => p.source_kind === "zip");
  useEffect(() => {
    if (zipProjects.length && !zipTarget) setZipTarget(zipProjects[0].id);
  }, [zipProjects.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setFileHash("");
    if (file) void digest(file).then(setFileHash);
  }, [file]);

  const tooBig = !!(file && maxUpload && file.size > maxUpload);
  const n = name.trim();
  const targetPid = kind === "zip" && zipMode === "update" && zipProjects.length ? zipTarget : null;
  const ready = kind === "local" ? !!path.trim() : kind === "github" ? !!url.trim() : !!file && !!fileHash && !tooBig;
  // A confirmation is only valid for exactly the inputs it was shown for.
  const key = useMemo(
    () => JSON.stringify(kind === "local" ? [kind, path.trim(), n] : kind === "github" ? [kind, url.trim(), ref.trim(), n] : [kind, fileHash, file?.name ?? "", n, targetPid]),
    [kind, path, url, ref, n, fileHash, file, targetPid],
  );
  const active = pending && pending.key === key ? pending.target : null;
  useEffect(() => setConfirmed(false), [key, active?.project_id, active?.exists]);

  const send = (dry: boolean, guard: WriteGuard, signal: AbortSignal) => {
    if (kind === "local") return api.analyzeLocal({ path: path.trim(), name: n || null, dry_run: dry, ...guard }, signal);
    if (kind === "github") return api.analyzeGithub({ url: url.trim(), ref: ref.trim() || null, name: n || null, dry_run: dry, ...guard }, signal);
    return api.analyzeZip(file!, { filename: file!.name, dry_run: dry, ...(n ? { name: n } : {}), ...(targetPid ? { target_project_id: targetPid } : {}), ...guard }, signal);
  };

  const run = async (dry: boolean) => {
    setWarning(null);
    if (!dry && active && !confirmed) {
      setWarning("Tick the confirmation to write to Neo4j Aura.");
      return;
    }
    const guard: WriteGuard = !dry && active ? { expected_project_id: active.project_id, confirm_update: !!active.exists } : {};
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setRunning(dry ? "dry" : "write");
    setError(null);
    try {
      const body = await send(dry, guard, c.signal);
      setReport(body);
      if (dry && body.source) setPending({ key, target: body.source });
      else setPending(null);
      if (!dry) {
        reloadProjects();
        refresh();
      }
    } catch (err) {
      if (isAbort(err)) return;
      if (err instanceof ApiError && err.status === 409 && err.code === "confirmation_required") {
        const target = err.detailObject?.target as SourceTarget | undefined;
        if (target) {
          setPending({ key, target });
          setReport(null);
          return;
        }
      }
      setError(err);
      setReport(null);
    } finally {
      if (ctrl.current === c) setRunning(null);
    }
  };

  const dryDone = !!report?.dry_run && !!active;
  const written = !!report && !report.dry_run;
  const flow: FlowStep[] = [
    { key: "source", label: "Choose source", state: ready ? "done" : "current" },
    { key: "dry", label: "Dry run", hint: "optional — never writes", state: running === "dry" ? "current" : dryDone || written ? "done" : active ? "skipped" : ready ? "current" : "todo" },
    { key: "confirm", label: "Confirm target", state: written ? "done" : active ? (confirmed ? "done" : "current") : "todo" },
    { key: "write", label: "Write to Aura", state: running === "write" ? "current" : written ? "done" : error && !running ? "error" : "todo" },
  ];
  const current = flow.findIndex((s) => s.state === "current");
  const stage = kind === "github" ? "Resolving the repository on GitHub, downloading, " : kind === "zip" ? "Uploading, validating and extracting the ZIP, " : "";

  return (
    <>
      <PageHeader eyebrow="Workspace" title="Analyze a repository">
        Build a project graph from a local directory, a public GitHub repository or a ZIP archive. A dry run never writes;
        writing to Neo4j Aura always asks you to confirm the target project first.
      </PageHeader>

      <Panel tone="glass" className="reveal">
        <ProgressFlow ariaLabel="Analysis workflow" steps={flow} current={current === -1 ? (written ? 3 : 0) : current} complete={written} showCount={false} maxLabels={8} />
      </Panel>

      <div className="analyze reveal">
        <Panel className="analyze__form">
          <div className="segmented" role="radiogroup" aria-label="Source">
            {KINDS.map((k) => (
              <button key={k.kind} role="radio" aria-checked={kind === k.kind} className={`segmented__opt${kind === k.kind ? " is-on" : ""}`} onClick={() => setKind(k.kind)}>{k.label}</button>
            ))}
          </div>
          <label className="field">
            <span className="field-label">Project name (optional)</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
          </label>
          {kind === "local" && (
            <label className="field">
              <span className="field-label">Local repository path (on the API server's machine)</span>
              <input className="input mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/repo" />
            </label>
          )}
          {kind === "github" && (
            <>
              <label className="field">
                <span className="field-label">Public GitHub repository URL</span>
                <input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo" maxLength={500} />
              </label>
              <label className="field">
                <span className="field-label">Branch, tag or commit (optional — empty = default branch)</span>
                <input className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} maxLength={200} />
              </label>
              <p className="hint">Public repositories only. No token is requested or stored; the archive is downloaded to a temporary folder and deleted after analysis.</p>
            </>
          )}
          {kind === "zip" && (
            <>
              <label className="field">
                <span className="field-label">ZIP archive{maxUpload ? ` (max ${fmtBytes(maxUpload)})` : ""}</span>
                <input className="input" type="file" accept=".zip,application/zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
              {file && <p className="hint">{file.name} · {fmtBytes(file.size)}{!fileHash && " · hashing…"}</p>}
              {tooBig && <Notice tone="danger">The file is {fmtNumber(file!.size)} bytes; the limit is {fmtNumber(maxUpload)}.</Notice>}
              <div className="segmented segmented--sm" role="radiogroup" aria-label="ZIP target">
                <button role="radio" aria-checked={zipMode === "new"} className={`segmented__opt${zipMode === "new" ? " is-on" : ""}`} onClick={() => setZipMode("new")}>New project</button>
                <button role="radio" aria-checked={zipMode === "update"} disabled={!zipProjects.length} className={`segmented__opt${zipMode === "update" ? " is-on" : ""}`} onClick={() => setZipMode("update")} title={zipProjects.length ? "Updating replaces that project's graph with this upload's files." : "No ZIP projects exist yet"}>
                  Update an existing ZIP project
                </button>
              </div>
              {zipMode === "update" && zipProjects.length > 0 && (
                <label className="field">
                  <span className="field-label">Project to update</span>
                  <select className="select" value={zipTarget} onChange={(e) => setZipTarget(e.target.value)}>
                    {zipProjects.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.id})</option>)}
                  </select>
                </label>
              )}
            </>
          )}

          {active && (
            <div className="confirm">
              <TargetCard t={active} />
              <label className="check check--strong">
                <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                {active.exists ? "Yes, update this existing project in Neo4j Aura" : "Yes, create this project in Neo4j Aura"}
              </label>
            </div>
          )}
          {warning && <Notice tone="warn">{warning}</Notice>}

          <div className="analyze__actions">
            <button className="btn" disabled={!ready || !!running} onClick={() => void run(true)}>
              {running === "dry" ? <Spinner size={14} /> : null} Dry Run (no database writes)
            </button>
            <button className="btn btn--copper" disabled={!ready || !!running} onClick={() => void run(false)}>
              {running === "write" ? <Spinner size={14} /> : null} Analyze &amp; Write to Neo4j Aura
            </button>
          </div>
          <p className="hint">Dry Run never writes. <i>Analyze &amp; Write</i> writes this project's graph to Neo4j Aura and asks you to confirm the target project first.</p>
          {running && (
            <div className="loading-inline" role="status">
              <Spinner size={14} /> {stage}scanning and parsing{running === "dry" ? " (dry run)…" : " and syncing to Neo4j Aura…"} The API reports no intermediate progress; this can take a few minutes for large repositories.
            </div>
          )}
          {error != null && <ErrorState error={error} title="Analysis failed" />}
        </Panel>
      </div>

      {report && <Report rep={report} />}
    </>
  );
}
