import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useApp, useArchitecture } from "../../context/AppContext";
import { useAction, useApi } from "../../hooks/useApi";
import { api } from "../../lib/api";
import { buildHash, navigate } from "../../lib/router";
import { fmtLocation } from "../../lib/format";
import { layerColor } from "../../lib/theme";
import { TOUR_KINDS, type TourKind, type TourStep } from "../../lib/types";
import { PageHeader, Panel } from "../../components/layout/Page";
import { ProgressFlow } from "../../components/flow/ProgressFlow";
import { Badge, EmptyState, ErrorState, Loading, Notice, ObservedBadge, Spinner } from "../../components/feedback/Feedback";
import { Prose } from "../../components/feedback/Prose";
import { ExplanationView } from "../explain/ExplanationView";

const KIND_INFO: Record<TourKind, { label: string; text: string; target: string | null }> = {
  overview: { label: "Project overview", text: "Layers and main components, in dependency order.", target: null },
  entry: { label: "Entry point", text: "Start where execution plausibly begins and follow resolved calls outward.", target: "Entry file" },
  component: { label: "Component", text: "Walk through one component's files and key symbols.", target: "Component" },
  execution: { label: "Execution path", text: "Follow static CALLS relationships from one function.", target: "Start function" },
  dependency: { label: "Dependencies", text: "What a component relies on, with the observed imports.", target: "Component" },
  impact: { label: "Change impact", text: "What depends on a component or file and may need review.", target: "Changed component or file" },
};

const STATIC_NOTE: Partial<Record<TourKind, string>> = {
  execution: "Derived from static CALLS relationships in the graph — not a runtime trace. Dynamic dispatch and callbacks are not visible.",
  entry: "Derived from static analysis of the graph — not a runtime trace.",
};

function StepCard({ step, pid, onTourComponent }: { step: TourStep; pid: string; onTourComponent: (c: string) => void }) {
  const [explaining, setExplaining] = useState(false);
  const exp = useApi(explaining && step.node_id ? (s) => api.explain(pid, step.node_id!, false, s) : null, [explaining, step.node_id]);
  const interp = useAction((signal, id: string) => api.explain(pid, id, true, signal));
  useEffect(() => {
    setExplaining(false);
    interp.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step.node_id, step.index]);
  const canExplain = !!step.node_id && ["Function", "Class", "File", "Module"].includes(step.node_label ?? "");
  const shown = interp.data ?? exp.data;
  return (
    <article className="step-card paper-surface" aria-live="polite" key={step.index}>
      <div className="step-card__eyebrow">
        <span className="step-card__num mono">{String(step.index + 1).padStart(2, "0")}</span>
        <span className="step-card__kind">{step.kind}</span>
        <ObservedBadge observed={step.observed} />
        {step.layer && <span className="layer-tag layer-tag--ink"><i style={{ background: layerColor(step.layer) }} />{step.layer}</span>}
      </div>
      <h2 className="step-card__title">{step.title}</h2>
      <p className="step-card__text">{step.explanation}</p>
      {step.location?.file_path && <div className="step-card__loc mono">{fmtLocation(step.location)}</div>}
      {step.evidence.length > 0 && (
        <div className="step-card__evidence">
          <div className="paper__section">Evidence</div>
          <ul className="evidence">{step.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
        </div>
      )}
      <div className="paper__actions">
        {canExplain && (
          <button className="btn btn--ink" aria-expanded={explaining} onClick={() => setExplaining((x) => !x)}>
            {explaining ? "Hide explanation" : `Explain ${step.node_label?.toLowerCase()}`}
          </button>
        )}
        {step.component && (
          <>
            {step.kind === "component" && <button className="btn btn--ink-ghost" onClick={() => onTourComponent(step.component!)}>Tour this component</button>}
            <a className="btn btn--ink-ghost" href={buildHash("architecture", pid, { component: step.component })}>Open in Architecture</a>
          </>
        )}
        {canExplain && <a className="btn btn--ink-ghost" href={buildHash("explain", pid, { node: step.node_id })}>Open in Explain</a>}
      </div>
      {explaining && (
        <div className="step-card__explain">
          {exp.error ? <ErrorState error={exp.error} onRetry={exp.reload} /> : !shown ? <Loading label="Collecting evidence…" rows={2} /> : (
            <>
              {interp.error != null && <ErrorState error={interp.error} title="Interpretation request failed" />}
              <ExplanationView exp={shown} compact onInterpret={() => step.node_id && void interp.run(step.node_id)} interpreting={interp.loading} />
            </>
          )}
        </div>
      )}
    </article>
  );
}

export function ToursPage() {
  const { pid, route, refreshKey } = useApp();
  const arch = useArchitecture();
  const params = route.params;
  const initialKind = (TOUR_KINDS as string[]).includes(params.kind) ? (params.kind as TourKind) : "overview";
  const [kind, setKind] = useState<TourKind>(initialKind);
  const [target, setTarget] = useState<string>(params.target ?? "");
  const [narrate, setNarrate] = useState(false);
  const [step, setStep] = useState(0);
  const [maxSeen, setMaxSeen] = useState(0);
  const tour = useAction((signal, k: TourKind, t: string | null, n: boolean) => api.tour(pid!, { kind: k, target: t, narrate: n }, signal));
  const fns = useApi(kind === "execution" ? (s) => api.functions(pid!, s) : null, [pid, kind === "execution", refreshKey]);
  const files = useApi(kind === "impact" ? (s) => api.files(pid!, s) : null, [pid, kind === "impact", refreshKey]);
  const stepRef = useRef<HTMLDivElement>(null);

  const comps = arch.data?.components.map((c) => c.name) ?? [];
  const options = useMemo((): { value: string; label: string }[] => {
    switch (kind) {
      case "component":
      case "dependency":
        return comps.map((c) => ({ value: c, label: c }));
      case "entry":
        return [{ value: "", label: "(auto-detect)" }, ...(arch.data?.entry_points ?? []).map((e) => ({ value: e, label: e }))];
      case "execution":
        return (fns.data ?? []).map((f) => ({ value: f.id, label: `${f.qualname}  —  ${f.file}` }));
      case "impact":
        return [
          ...comps.map((c) => ({ value: c, label: `component: ${c}` })),
          ...(files.data ?? []).map((f) => ({ value: f.id, label: `file: ${f.path}` })),
        ];
      default:
        return [];
    }
  }, [kind, comps.join("|"), arch.data, fns.data, files.data]);

  // keep the target valid for the selected kind (a deep-linked target is kept even if not listed)
  useEffect(() => {
    if (kind === "overview") return;
    if (target && (options.some((o) => o.value === target) || target === params.target)) return;
    if (kind !== "entry" && options.length) setTarget(options[0].value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, options]);

  const start = async (k = kind, t: string | null = target || null, n = narrate) => {
    setStep(0);
    setMaxSeen(0);
    const res = await tour.run(k, k === "overview" ? null : t, n);
    if (res) navigate("tours", pid, { kind: k, target: k === "overview" ? undefined : t ?? undefined }, true);
  };

  // Deep links (?start=1, also from inline explanations on this page) start the tour
  // immediately — never with narration, which always needs an explicit choice.
  useEffect(() => {
    if (params.start !== "1") return;
    const k = (TOUR_KINDS as string[]).includes(params.kind) ? (params.kind as TourKind) : "overview";
    setKind(k);
    setTarget(params.target ?? "");
    void start(k, params.target ?? null, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.start, params.kind, params.target]);

  const t = tour.data;
  const steps = t?.steps ?? [];
  const i = Math.min(step, Math.max(0, steps.length - 1));
  const go = (n: number) => {
    const next = Math.max(0, Math.min(steps.length - 1, n));
    setStep(next);
    setMaxSeen((m) => Math.max(m, next));
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || !steps.length) return;
      if (e.key === "ArrowRight") go(i + 1);
      if (e.key === "ArrowLeft") go(i - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const needsTarget = kind !== "overview" && kind !== "entry";
  const listLoading = (kind === "execution" && fns.loading) || (kind === "impact" && files.loading) || ((kind === "component" || kind === "dependency") && arch.loading);

  return (
    <>
      <PageHeader eyebrow="Intelligence" title="Guided tours">
        Deterministic walkthroughs built from the project graph. Each step shows its evidence and whether it was observed or inferred.
      </PageHeader>

      <Panel tone="glass" className="tour-setup reveal">
        <div className="segmented" role="radiogroup" aria-label="Tour type">
          {TOUR_KINDS.map((k) => (
            <button key={k} role="radio" aria-checked={kind === k} className={`segmented__opt${kind === k ? " is-on" : ""}`} onClick={() => setKind(k)}>
              {KIND_INFO[k].label}
            </button>
          ))}
        </div>
        <p className="hint">{KIND_INFO[kind].text}</p>
        <form className="form-row" onSubmit={(e) => { e.preventDefault(); void start(); }}>
          {KIND_INFO[kind].target && (
            <label className="field grow">
              <span className="field-label">{KIND_INFO[kind].target}</span>
              {listLoading ? (
                <div className="loading-inline"><Spinner size={14} /> Loading choices…</div>
              ) : (
                <select className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
                  {target && !options.some((o) => o.value === target) && <option value={target}>{target}</option>}
                  {options.map((o) => <option key={o.value || "auto"} value={o.value}>{o.label}</option>)}
                </select>
              )}
            </label>
          )}
          <label className="check" title="Ask the configured LLM to narrate the deterministic steps (one live LLM request).">
            <input type="checkbox" checked={narrate} onChange={(e) => setNarrate(e.target.checked)} />
            Narrate with LLM
          </label>
          <button className="btn btn--primary" type="submit" disabled={tour.loading || (needsTarget && !target)}>
            {tour.loading ? <><Spinner size={14} /> Building…</> : "Start tour"}
          </button>
          {t && t.kind !== "overview" && (
            <button type="button" className="btn btn--ghost" onClick={() => { setKind("overview"); void start("overview", null, false); }}>Back to the overview tour</button>
          )}
        </form>
        {arch.error != null && <ErrorState error={arch.error} onRetry={arch.reload} title="Architecture unavailable (component choices)" />}
      </Panel>

      {tour.error != null && <ErrorState error={tour.error} />}
      {tour.loading && !t && <Loading label={`Building the tour from the graph${narrate ? " and asking the LLM to narrate" : ""}…`} rows={3} />}

      {t && (
        <div className={`tour reveal${tour.loading ? " is-stale" : ""}`} ref={stepRef}>
          <header className="tour__head">
            <div className="eyebrow">{KIND_INFO[t.kind]?.label ?? t.kind} tour{t.target ? <> · <span className="mono">{t.target}</span></> : null}</div>
            <h2 className="tour__title">{t.title}</h2>
            <p className="tour__purpose">{t.purpose}</p>
            {STATIC_NOTE[t.kind] && <p className="hint">{STATIC_NOTE[t.kind]}</p>}
          </header>
          {t.notes.map((n) => <Notice key={n} tone="info">{n}</Notice>)}
          {t.llm_error && (
            <Notice tone="warn" title={`No narration (${t.llm_error.code})`}>
              {t.llm_error.message} The steps below are complete without it.
            </Notice>
          )}
          {t.narrative && (
            <Panel className="narration">
              <div className="narration__label"><span className="tag tag--ai">LLM</span> Narration — generated from the steps below; the steps are the evidence.</div>
              <Prose text={t.narrative} />
            </Panel>
          )}

          {steps.length ? (
            <>
              <ProgressFlow
                ariaLabel="Tour progress"
                steps={steps.map((s) => ({ key: String(s.index), label: s.title, hint: s.observed ? "observed" : "inferred", state: s.index === i ? "current" : s.index <= maxSeen ? "done" : "todo" }))}
                current={i}
                complete={maxSeen >= steps.length - 1 && i === steps.length - 1}
                onSelect={go}
                controls={
                  <>
                    <button className="btn btn--sm" onClick={() => go(i - 1)} disabled={i === 0} aria-label="Previous step">← Previous</button>
                    <button className="btn btn--sm btn--primary" onClick={() => go(i + 1)} disabled={i >= steps.length - 1} aria-label="Next step">Next →</button>
                  </>
                }
                footer={t.truncated ? <Notice tone="warn" title="Tour truncated">The backend stopped after {steps.length} steps; the tour does not cover every related node.</Notice> : null}
              />
              <div className="tour__body">
                <ol className="timeline" aria-label="All steps">
                  {steps.map((s) => (
                    <li key={s.index} className={`timeline__item${s.index === i ? " is-current" : ""}${s.index <= maxSeen ? " is-seen" : ""}`} style={{ "--i": Math.min(s.index, 12) } as CSSProperties}>
                      <button onClick={() => go(s.index)} aria-current={s.index === i ? "step" : undefined}>
                        <span className="timeline__num mono">{s.index + 1}</span>
                        <span className="timeline__title">{s.title}</span>
                        {!s.observed && <Badge tone="warn">inferred</Badge>}
                      </button>
                    </li>
                  ))}
                </ol>
                <StepCard step={steps[i]} pid={pid!} onTourComponent={(c) => { setKind("component"); setTarget(c); void start("component", c, false); }} />
              </div>
              <p className="hint">Tip: use ← and → to move between steps.</p>
            </>
          ) : (
            <EmptyState title="This tour has no steps">See the notes above for what the project is missing.</EmptyState>
          )}
        </div>
      )}
    </>
  );
}
