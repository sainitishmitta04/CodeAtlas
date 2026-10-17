import { useState } from "react";
import { useApp } from "../../context/AppContext";
import { useAction, useApi, useDebounced } from "../../hooks/useApi";
import { api } from "../../lib/api";
import { navigate } from "../../lib/router";
import { fmtLocation } from "../../lib/format";
import { labelColor } from "../../lib/theme";
import { PageHeader, Panel } from "../../components/layout/Page";
import { EmptyState, ErrorState, Loading, Spinner } from "../../components/feedback/Feedback";
import { ExplanationView } from "./ExplanationView";
import type { Explanation } from "../../lib/types";

export function ExplainPage() {
  const { pid, route, focus, refreshKey } = useApp();
  const nodeId = route.params.node ?? null;
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const matches = useApi(dq ? (s) => api.search(pid!, { q: dq, limit: 15 }, s) : null, [pid, dq]);
  const exp = useApi(nodeId ? (s) => api.explain(pid!, nodeId, false, s) : null, [pid, nodeId, refreshKey]);
  const interp = useAction((signal, id: string) => api.explain(pid!, id, true, signal));
  const shown: Explanation | null = interp.data && interp.data.facts.node_id === nodeId ? interp.data : exp.data;

  const open = (id: string) => {
    interp.reset();
    navigate("explain", pid, { node: id });
  };

  return (
    <>
      <PageHeader eyebrow="Intelligence" title="Explain a symbol">
        Explanations separate <b>observed facts</b> (graph and verified source), <b>unknowns</b>, and an optional <b>LLM interpretation</b> written only from those facts.
      </PageHeader>

      <Panel tone="glass" className="explain-finder reveal">
        <label className="field grow">
          <span className="field-label">Find a function, method, class, file or module</span>
          <input
            className="input input--lg"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. sync_project, Cart, graph/db.py"
            aria-controls="explain-matches"
          />
        </label>
        {focus && focus.props.project_id === pid && focus.label !== "Project" && (
          <button className="btn" onClick={() => open(focus.id)}>Use focused graph node · <span className="mono">{focus.caption.slice(0, 32)}</span></button>
        )}
        {dq && (
          <div id="explain-matches" className="finder-results" aria-live="polite">
            {matches.loading ? (
              <div className="loading-inline"><Spinner size={14} /> Searching…</div>
            ) : matches.error ? (
              <ErrorState error={matches.error} />
            ) : matches.data && matches.data.results.length ? (
              <ul>
                {matches.data.results.map((r) => (
                  <li key={r.node_id}>
                    <button className={`finder-hit${r.node_id === nodeId ? " is-active" : ""}`} onClick={() => { open(r.node_id); setQ(""); }}>
                      <i style={{ background: labelColor(r.label) }} />
                      <span className="finder-hit__label">{r.label}</span>
                      <span className="mono finder-hit__name">{r.display}</span>
                      <span className="mono muted small">{fmtLocation(r.location)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No symbol matches that name in this project.</p>
            )}
          </div>
        )}
      </Panel>

      {!nodeId ? (
        <EmptyState title="Choose a symbol" icon="¶">
          Search above, select a node in the Graph, or follow an Explain link from Search, Tours or Architecture.
        </EmptyState>
      ) : exp.error ? (
        <ErrorState error={exp.error} onRetry={exp.reload} />
      ) : !shown ? (
        <Loading label="Collecting evidence from the project snapshot…" rows={4} />
      ) : (
        <Panel className="reveal explain-panel">
          {interp.error != null && <ErrorState error={interp.error} title="Interpretation request failed" />}
          <ExplanationView exp={shown} onInterpret={() => void interp.run(nodeId)} interpreting={interp.loading} />
        </Panel>
      )}
    </>
  );
}
