import type { ReactNode } from "react";
import { useApp } from "../../context/AppContext";
import { buildHash, navigate } from "../../lib/router";
import { fmtLocation } from "../../lib/format";
import { labelColor, layerColor } from "../../lib/theme";
import type { Explanation, Ref } from "../../lib/types";
import { Badge, Notice, Spinner } from "../../components/feedback/Feedback";
import { Prose } from "../../components/feedback/Prose";

function RefList({ title, refs, total }: { title: string; refs: Ref[]; total?: number }) {
  const { pid } = useApp();
  if (!refs.length) return null;
  return (
    <div className="refs">
      <div className="refs__title">
        {title}
        <span className="refs__count">{total && total > refs.length ? `${refs.length} of ${total}` : refs.length}</span>
      </div>
      <ul>
        {refs.map((r, i) => (
          <li key={`${r.node_id}-${i}`}>
            <i className="refs__dot" style={{ background: labelColor(r.label) }} title={r.label} />
            {r.label !== "Module" ? (
              <a className="mono" href={buildHash("explain", pid, { node: r.node_id })}>{r.display}</a>
            ) : (
              <span className="mono">{r.display}</span>
            )}
            <span className="refs__meta">
              {r.lines.length > 0 && <span>line{r.lines.length > 1 ? "s" : ""} {r.lines.slice(0, 5).join(", ")}{r.lines.length > 5 ? "…" : ""}</span>}
              {r.confidence && <Badge tone={r.confidence === "exact" ? "sage" : "warn"}>{r.confidence}</Badge>}
              {r.layer && <span className="layer-tag"><i style={{ background: layerColor(r.layer) }} />{r.layer}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="fact">
      <div className="fact__label">{label}</div>
      <div className="fact__value">{children}</div>
    </div>
  );
}

function CodeExcerpt({ text, start }: { text: string; start: number }) {
  const lines = text.replace(/\n$/, "").split("\n");
  return (
    <pre className="code code--source" tabIndex={0} aria-label="Source excerpt">
      <code>
        {lines.map((l, i) => (
          <span key={i} className="code__line">
            <span className="code__ln" aria-hidden="true">{start + i}</span>
            <span className="code__text">{l || " "}</span>
          </span>
        ))}
      </code>
    </pre>
  );
}

interface Props {
  exp: Explanation;
  onInterpret?: () => void;
  interpreting?: boolean;
  interpretError?: unknown;
  compact?: boolean;
}

/** Observed facts, unknowns and (separately) an optional LLM interpretation for one symbol. */
export function ExplanationView({ exp, onInterpret, interpreting, compact }: Props) {
  const { pid } = useApp();
  const f = exp.facts;
  const c = f.counts ?? {};
  const loc = f.location;
  return (
    <article className={`explain${compact ? " explain--compact" : ""}`} aria-label={`Explanation of ${f.qualname ?? f.name}`}>
      <header className="explain__head">
        <div className="explain__kind">
          <span className="badge" style={{ color: labelColor(f.label), borderColor: labelColor(f.label) }}>{f.kind}</span>
          {f.layer && (
            <span className="layer-tag" title="Inferred layer">
              <i style={{ background: layerColor(f.layer) }} />{f.layer} · inferred
            </span>
          )}
          {f.component && <a className="chip mono" href={buildHash("architecture", pid, { component: f.component })}>{f.component}</a>}
        </div>
        <h2 className="explain__title mono">{f.qualname ?? f.name}</h2>
        <div className="explain__loc mono">{fmtLocation(loc)}</div>
      </header>

      <div className="explain__grid">
        <section className="explain__facts" aria-labelledby={`facts-${f.node_id}`}>
          <h3 className="explain__section" id={`facts-${f.node_id}`}><span className="tag tag--observed">Observed</span> Facts from the graph</h3>
          {f.signature && <pre className="code code--sig"><code>{f.signature}</code></pre>}
          <div className="facts">
            {f.decorators.length > 0 && <Fact label="Decorators">{f.decorators.map((d) => <code key={d} className="inline-code">{d}</code>)}</Fact>}
            {f.bases.length > 0 && <Fact label="Bases">{f.bases.map((d) => <code key={d} className="inline-code">{d}</code>)}</Fact>}
            {f.external_calls.length > 0 && <Fact label="External calls">{f.external_calls.map((d) => <code key={d} className="inline-code">{d}</code>)}</Fact>}
            {f.unresolved_calls.length > 0 && <Fact label="Unresolved calls">{f.unresolved_calls.map((d) => <code key={d} className="inline-code inline-code--warn">{d}</code>)}</Fact>}
          </div>
          {f.docstring && (
            <blockquote className="docstring">
              <div className="docstring__label">Docstring</div>
              {f.docstring.slice(0, 1500)}
            </blockquote>
          )}
          <RefList title="Belongs to" refs={f.belongs_to} />
          <RefList title="Calls" refs={f.calls} total={c.calls} />
          <RefList title="Called by" refs={f.callers} total={c.callers ?? c.callers_of_methods} />
          <RefList title="Methods" refs={f.methods} total={c.methods} />
          <RefList title="Parent classes" refs={f.parents} />
          <RefList title="Subclasses" refs={f.subclasses} total={c.subclasses} />
          <RefList title="Defines" refs={f.defines} total={c.defines} />
          <RefList title="Imports" refs={f.imports} total={c.imports} />
          <RefList title="Imported by" refs={f.imported_by} total={c.imported_by} />
          {f.side_effect_hints.length > 0 && (
            <div className="refs">
              <div className="refs__title">Possible side effects <span className="refs__count">from observed calls</span></div>
              <ul className="plain-list">{f.side_effect_hints.map((h) => <li key={h}>{h}</li>)}</ul>
            </div>
          )}
        </section>

        <aside className="explain__side">
          <section className="unknowns" aria-labelledby={`unk-${f.node_id}`}>
            <h3 className="explain__section" id={`unk-${f.node_id}`}><span className="tag tag--unknown">Unknown</span> Not visible to static analysis</h3>
            {f.unknowns.length ? <ul className="plain-list">{f.unknowns.map((u) => <li key={u}>{u}</li>)}</ul> : <p className="muted">No specific unknowns recorded.</p>}
          </section>
          {f.source_excerpt ? (
            <section aria-label="Source excerpt">
              <h3 className="explain__section">
                <span className="tag tag--observed">Source</span>
                {f.source_excerpt.verified ? "Verified unchanged since the last sync" : "Excerpt"} · lines {f.source_excerpt.start_line}–{f.source_excerpt.end_line}
              </h3>
              <CodeExcerpt text={f.source_excerpt.text} start={f.source_excerpt.start_line} />
            </section>
          ) : (
            <p className="hint">No source excerpt: source text is only read for local projects whose file is unchanged since the last sync.</p>
          )}
          <div className="explain__actions">
            {f.label === "Function" && (
              <button className="btn btn--sm" onClick={() => navigate("tours", pid, { kind: "execution", target: f.node_id, start: "1" })}>Execution-path tour</button>
            )}
            {["Function", "Class", "File"].includes(f.label) && (
              <button className="btn btn--sm" onClick={() => navigate("tours", pid, { kind: "impact", target: f.node_id, start: "1" })}>Impact tour</button>
            )}
            {["Function", "Class", "File", "Module"].includes(f.label) && (
              <a className="btn btn--sm btn--ghost" href={buildHash("paths", pid, { impact: f.node_id, label: f.label })}>Impact analysis</a>
            )}
          </div>
        </aside>
      </div>

      <section className="interpretation" aria-labelledby={`interp-${f.node_id}`}>
        <h3 className="explain__section" id={`interp-${f.node_id}`}><span className="tag tag--ai">LLM</span> Interpretation</h3>
        {exp.interpretation ? (
          <div className="interpretation__body">
            <p className="hint">Generated by the LLM from the observed facts above — verify against them. It is not itself evidence.</p>
            <Prose text={exp.interpretation} />
          </div>
        ) : exp.llm_error ? (
          <Notice tone="warn" title={`No LLM interpretation (${exp.llm_error.code})`}>
            {exp.llm_error.message} The observed facts above are complete without it.
            {exp.llm_error.hint ? <div className="hint">{exp.llm_error.hint}</div> : null}
          </Notice>
        ) : (
          <p className="muted">No interpretation requested — the facts above are the evidence.</p>
        )}
        {onInterpret && (
          <button className="btn btn--copper" onClick={onInterpret} disabled={interpreting}>
            {interpreting ? <><Spinner size={14} /> Asking the LLM…</> : exp.interpretation ? "Regenerate LLM interpretation" : "Generate LLM interpretation"}
          </button>
        )}
      </section>
    </article>
  );
}
