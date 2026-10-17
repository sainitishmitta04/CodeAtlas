import { useApp } from "../../context/AppContext";
import { useAction } from "../../hooks/useApi";
import { api, API_BASE } from "../../lib/api";
import { fmtBytes, fmtWhen, titleCase } from "../../lib/format";
import { PageHeader, Panel, Section } from "../../components/layout/Page";
import { Badge, ErrorState, Loading, Notice, Spinner } from "../../components/feedback/Feedback";

export function DiagnosticsPage() {
  const { health, healthError, reloadHealth, pid, project } = useApp();
  const integrity = useAction((signal, id: string) => api.integrity(id, signal));
  const last = health?.llm.last_call;

  return (
    <>
      <PageHeader eyebrow="Workspace" title="Diagnostics" actions={<button className="btn" onClick={reloadHealth}>Re-check health</button>}>
        Service status reported by the CodeAtlas API. The health check pings Neo4j but never calls the LLM provider.
      </PageHeader>

      {healthError ? (
        <ErrorState error={healthError} onRetry={reloadHealth} />
      ) : !health ? (
        <Loading label="Checking services…" rows={2} />
      ) : (
        <div className="grid grid--2">
          <Section title="Neo4j" index={1}>
            <Panel>
              <div className="diag-status">
                <Badge tone={health.neo4j.ok ? "sage" : "danger"}>{health.neo4j.ok ? "connected" : "unavailable"}</Badge>
                {health.neo4j.error && <span className="muted">{health.neo4j.error}</span>}
              </div>
              <dl className="kv">
                <div className="kv__row"><dt>Max upload</dt><dd className="mono">{fmtBytes(health.max_upload_bytes)}</dd></div>
                <div className="kv__row"><dt>API base (browser)</dt><dd className="mono">{API_BASE}</dd></div>
              </dl>
            </Panel>
          </Section>
          <Section title="LLM" index={2}>
            <Panel>
              <div className="diag-status">
                <Badge tone={!health.llm.configured ? "neutral" : last?.state === "ok" ? "sage" : last?.state === "failed" ? "danger" : "neutral"}>
                  {!health.llm.configured ? "not configured" : last?.state === "ok" ? "last call OK" : last?.state === "failed" ? "last call failed" : "not verified yet"}
                </Badge>
              </div>
              <dl className="kv">
                <div className="kv__row"><dt>Model</dt><dd className="mono">{health.llm.model}</dd></div>
                <div className="kv__row"><dt>Provider host</dt><dd className="mono">{health.llm.base_url_host}</dd></div>
                <div className="kv__row"><dt>Base URL valid</dt><dd>{health.llm.base_url_valid ? "yes" : "no"}</dd></div>
                {last?.at && <div className="kv__row"><dt>Last call</dt><dd>{fmtWhen(last.at)}</dd></div>}
                {last?.code && <div className="kv__row"><dt>Last error</dt><dd className="mono">{last.code}{last.provider_status ? ` (HTTP ${last.provider_status})` : ""}</dd></div>}
                {last?.request_id && <div className="kv__row"><dt>Request id</dt><dd className="mono">{last.request_id}</dd></div>}
              </dl>
              <p className="hint">The status reflects the most recent real LLM request (Q&amp;A, narration or interpretation); no key material is ever shown.</p>
            </Panel>
          </Section>
          <Section title="Configuration" index={3}>
            <Panel>
              <dl className="kv">
                <div className="kv__row"><dt>.env file found</dt><dd>{health.config.env_file_found ? "yes" : "no"}</dd></div>
              </dl>
              {health.config.shell_vars_ignored_in_favour_of_env_file.length > 0 && (
                <Notice tone="info" title="Shell variables ignored in favour of the workspace .env">
                  <span className="mono">{health.config.shell_vars_ignored_in_favour_of_env_file.join(", ")}</span>
                </Notice>
              )}
            </Panel>
          </Section>
          <Section title="Graph integrity" kicker={project ? project.name : "no project"} index={4}>
            <Panel>
              <p className="muted">Read-only consistency checks over the selected project's graph.</p>
              <button className="btn" disabled={!pid || integrity.loading} onClick={() => pid && void integrity.run(pid)}>
                {integrity.loading ? <Spinner size={14} /> : null} Run integrity checks
              </button>
              {integrity.error != null && <ErrorState error={integrity.error} />}
              {integrity.data && (
                <dl className="kv">
                  {Object.entries(integrity.data).map(([k, v]) => (
                    <div key={k} className="kv__row">
                      <dt>{titleCase(k)}</dt>
                      <dd><Badge tone={v === 0 ? "sage" : "danger"}>{v === 0 ? "ok" : `${v} violation${v === 1 ? "" : "s"}`}</Badge></dd>
                    </div>
                  ))}
                </dl>
              )}
            </Panel>
          </Section>
        </div>
      )}
    </>
  );
}
