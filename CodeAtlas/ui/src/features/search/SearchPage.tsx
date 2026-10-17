import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useApp, useArchitecture } from "../../context/AppContext";
import { useDebounced } from "../../hooks/useApi";
import { api } from "../../lib/api";
import { isAbort } from "../../lib/errors";
import { buildHash, navigate } from "../../lib/router";
import { fmtLocation } from "../../lib/format";
import { labelColor, layerColor } from "../../lib/theme";
import type { SearchResponse, SearchResult } from "../../lib/types";
import { PageHeader } from "../../components/layout/Page";
import { EmptyState, ErrorState, Spinner } from "../../components/feedback/Feedback";

const SYMBOL_TYPES = ["Function", "Class", "File", "Module"];
const PAGE = 10;

export function SearchPage() {
  const { pid, route, refreshKey } = useApp();
  const arch = useArchitecture();
  const [q, setQ] = useState(route.params.q ?? "");
  const [labels, setLabels] = useState<string[]>([]);
  const [layers, setLayers] = useState<string[]>([]);
  const dq = useDebounced(q.trim(), 300);
  const [resp, setResp] = useState<SearchResponse | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [active, setActive] = useState(-1);
  const ctrl = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLOListElement>(null);

  const fetchPage = async (offset: number) => {
    if (!pid || !dq) return;
    ctrl.current?.abort(); // stale requests are cancelled
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    try {
      const r = await api.search(pid, { q: dq, labels, layers, limit: PAGE, offset }, c.signal);
      setResp(r);
      setResults((prev) => (offset === 0 ? r.results : [...prev, ...r.results]));
      if (offset === 0) setActive(-1);
    } catch (err) {
      if (!isAbort(err)) setError(err);
    } finally {
      if (ctrl.current === c) setLoading(false);
    }
  };

  useEffect(() => {
    if (!dq) {
      ctrl.current?.abort();
      setResp(null);
      setResults([]);
      setLoading(false);
      setError(null);
      return;
    }
    void fetchPage(0);
    navigate("search", pid, { q: dq }, true);
    return () => ctrl.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid, dq, labels.join(","), layers.join(","), refreshKey]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const open = (r: SearchResult) => {
    if (r.label === "Module") navigate("paths", pid, { impact: r.node_id, label: "Module" });
    else navigate("explain", pid, { node: r.node_id });
  };

  const onKey = (e: KeyboardEvent) => {
    if (!results.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(results.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(-1, a - 1));
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      open(results[active]);
    }
  };
  useEffect(() => {
    if (active >= 0) listRef.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const layerOptions = arch.data?.layers.map((l) => l.layer) ?? [];

  return (
    <>
      <PageHeader eyebrow="Intelligence" title="Search">
        <b>Lexical + graph search.</b> It matches identifiers, paths, docstrings, parameters, decorators and called or imported names,
        with typo tolerance, a synonym table and graph-based ranking. It is not embedding-based semantic search.
      </PageHeader>

      <div className="search-box reveal">
        <span className="search-box__icon" aria-hidden="true">⌕</span>
        <input
          className="search-box__input"
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          placeholder="e.g. where is the database connection initialized?"
          aria-label="Search the project"
          aria-controls="search-results"
          aria-activedescendant={active >= 0 ? `sr-${active}` : undefined}
          maxLength={300}
          autoFocus
        />
        {loading && <Spinner size={18} label="Searching" />}
        <span className="search-box__mode mono" title={resp?.note}>{resp?.mode ?? "lexical+graph"}</span>
      </div>

      <div className="search-filters reveal" style={{ "--i": 1 } as CSSProperties}>
        <div className="filter-group" role="group" aria-label="Symbol types">
          <span className="field-label">Symbol types</span>
          {SYMBOL_TYPES.map((l) => (
            <button key={l} className={`toggle${labels.includes(l) ? " is-on" : ""}`} aria-pressed={labels.includes(l)} onClick={() => toggle(labels, setLabels, l)}>
              <i style={{ background: labels.includes(l) ? labelColor(l) : "transparent", borderColor: labelColor(l) }} />{l}
            </button>
          ))}
        </div>
        {layerOptions.length > 0 && (
          <div className="filter-group" role="group" aria-label="Layers">
            <span className="field-label">Layers</span>
            {layerOptions.map((l) => (
              <button key={l} className={`toggle${layers.includes(l) ? " is-on" : ""}`} aria-pressed={layers.includes(l)} onClick={() => toggle(layers, setLayers, l)}>
                <i style={{ background: layers.includes(l) ? layerColor(l) : "transparent", borderColor: layerColor(l) }} />{l}
              </button>
            ))}
          </div>
        )}
      </div>

      {resp && Object.keys(resp.expanded_terms).length > 0 && (
        <p className="hint terms">
          Query terms: <span className="mono">{resp.terms.join(", ")}</span> · expanded:{" "}
          {Object.entries(resp.expanded_terms).map(([t, v]) => <span key={t} className="mono">{t} → {v.join(", ")}; </span>)}
        </p>
      )}

      {error != null && <ErrorState error={error} onRetry={() => void fetchPage(0)} />}

      {!dq ? (
        <EmptyState title="Describe what you are looking for" icon="⌕">
          Try a function name, a file path or a plain description such as “parse configuration” or “send email”. Use ↑ ↓ and Enter to open a result.
        </EmptyState>
      ) : resp && !results.length && !loading ? (
        <EmptyState title="No matches">Try other words, or remove the filters.</EmptyState>
      ) : (
        <>
          {resp && <div className="search-count" aria-live="polite">{resp.total} match{resp.total === 1 ? "" : "es"} · showing {results.length}</div>}
          <ol className="results" id="search-results" ref={listRef} role="listbox" aria-label="Search results">
            {results.map((r, i) => (
              <li
                key={r.node_id}
                id={`sr-${i}`}
                role="option"
                aria-selected={i === active}
                className={`result${i === active ? " is-active" : ""}`}
                style={{ "--i": Math.min(i % PAGE, 9) } as CSSProperties}
                onMouseEnter={() => setActive(i)}
              >
                <div className="result__main">
                  <div className="result__head">
                    <span className="result__rank mono">{i + 1}</span>
                    <span className="badge" style={{ color: labelColor(r.label), borderColor: labelColor(r.label) }}>{r.label}</span>
                    <span className="result__name mono">{r.display}</span>
                    {r.layer && <span className="layer-tag"><i style={{ background: layerColor(r.layer) }} />{r.layer}</span>}
                  </div>
                  <div className="result__loc mono">{fmtLocation(r.location)}{r.component ? ` · ${r.component}` : ""}</div>
                  {r.summary && <p className="result__summary">“{r.summary}”</p>}
                  <div className="result__why"><span>Why it matched</span>{r.matched.map((m) => <em key={m}>{m}</em>)}</div>
                </div>
                <div className="result__side">
                  <span className="result__score mono" title="Ranking score from the backend">{r.score.toFixed(2)}</span>
                  {r.label !== "Module" ? (
                    <a className="btn btn--sm" href={buildHash("explain", pid, { node: r.node_id })} tabIndex={-1}>Explain</a>
                  ) : (
                    <a className="btn btn--sm btn--ghost" href={buildHash("paths", pid, { impact: r.node_id, label: "Module" })} tabIndex={-1}>Importers</a>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {resp && resp.total > results.length && (
            <button className="btn btn--ghost btn--block" disabled={loading} onClick={() => void fetchPage(results.length)}>
              {loading ? <Spinner size={14} /> : null} Show more results ({resp.total - results.length} remaining)
            </button>
          )}
        </>
      )}
    </>
  );
}
