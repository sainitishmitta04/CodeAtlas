import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useApi } from "../../hooks/useApi";
import { api } from "../../lib/api";
import { ApiError, isAbort } from "../../lib/errors";
import type { Answer, LlmErrorDetail } from "../../lib/types";
import { PageHeader, Panel } from "../../components/layout/Page";
import { Graph } from "../../components/graph/LazyGraph";
import { Collapsible, CypherBlock, ErrorState, Notice, Spinner, Badge } from "../../components/feedback/Feedback";
import { Prose } from "../../components/feedback/Prose";

const TEMPLATES = [
  "What does this repository do?",
  "Which files contain the main functionality?",
  "What depends on `{focus}`?",
  "Which functions are connected to `{focus}`?",
  "What could be affected if `{focus}` changes?",
];

interface Turn { question: string; answer: Answer | null; error: unknown }

function Evidence({ answer, idx }: { answer: Answer; idx: number }) {
  return (
    <>
      <Collapsible title={`Evidence facts (${answer.facts.length})`} meta={<span>retrieved from Neo4j</span>}>
        <pre className="code code--block facts-block"><code>{answer.facts.join("\n") || "(no facts)"}</code></pre>
      </Collapsible>
      {answer.evidence.nodes.length > 0 && (
        <Graph graph={answer.evidence} height={380} compact ariaLabel={`Evidence graph for answer ${idx + 1}`} />
      )}
      <CypherBlock cypher={answer.evidence.cypher} params={answer.evidence.params} />
    </>
  );
}

function LlmFailure({ err, idx }: { err: ApiError; idx: number }) {
  const d = (err.detailObject ?? {}) as unknown as Partial<LlmErrorDetail>;
  const isLlm = err.component === "llm";
  const diag = Object.fromEntries(
    (["code", "provider_status", "request_id", "provider_message", "attempts", "retryable"] as const)
      .filter((k) => d[k] != null)
      .map((k) => [k, d[k]]),
  );
  return (
    <div className="answer answer--failed">
      <Notice tone="danger" title={isLlm ? `No answer: the LLM request failed (${d.code ?? err.status})` : "No answer"}>
        {err.message}
        {d.hint && <div className="hint">{d.hint}</div>}
      </Notice>
      {Object.keys(diag).length > 0 && (
        <Collapsible title="Diagnostics" meta={<span>contains no credentials</span>}>
          <pre className="code code--block"><code>{JSON.stringify({ http_status: err.status, component: err.component, ...diag }, null, 2)}</code></pre>
        </Collapsible>
      )}
      {d.graph_context && (
        <>
          <div className="answer__label">Graph facts retrieved from Neo4j <b>(not an LLM answer)</b></div>
          <Evidence answer={d.graph_context} idx={idx} />
        </>
      )}
    </div>
  );
}

export function AskPage() {
  const { pid, focus, reloadHealth } = useApp();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const focusInProject = focus && (focus.props.project_id === pid) ? focus : null;
  const stats = useApi((s) => api.stats(pid!, s), [pid]);
  const [asking, setAsking] = useState(false);
  const ctrl = useRef<AbortController | null>(null);
  useEffect(() => () => ctrl.current?.abort(), []);

  // Suggested targets come from the real graph: the focused node, else the most imported file.
  const focusName = useMemo(() => {
    if (focusInProject) return String(focusInProject.props.path ?? focusInProject.props.qualname ?? focusInProject.caption);
    return stats.data?.top_imported_files[0]?.path ?? null;
  }, [focusInProject, stats.data]);
  const suggestions = TEMPLATES.filter((t) => focusName || !t.includes("{focus}")).map((t) => t.replace("{focus}", focusName ?? ""));

  const submit = async (q: string) => {
    const text = q.trim();
    if (text.length < 3 || asking || !pid) return;
    setQuestion("");
    const idx = turns.length;
    setTurns((t) => [...t, { question: text, answer: null, error: null }]);
    const c = new AbortController();
    ctrl.current = c;
    setAsking(true);
    let update: Partial<Turn>;
    try {
      const answer = await api.ask({ project_id: pid, question: text, focus_id: focusInProject?.id ?? null, focus_label: focusInProject?.label ?? null }, c.signal);
      update = { answer };
    } catch (error) {
      if (isAbort(error)) return;
      update = { error };
    } finally {
      setAsking(false);
    }
    setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, ...update } : turn)));
    reloadHealth(); // the sidebar shows this call's LLM outcome
  };

  return (
    <>
      <PageHeader eyebrow="Intelligence" title="Questions & answers">
        Answers use fixed Cypher retrieval patterns over the stored graph. The LLM only writes prose from the retrieved facts, and each
        question is answered independently — there is no conversation memory.
      </PageHeader>

      <div className="qa">
        <div className="qa__thread" aria-live="polite">
          {!turns.length && (
            <Panel tone="flat" className="qa__intro">
              <div className="panel__title">Suggested questions</div>
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button key={s} className="suggestion" onClick={() => void submit(s)}>{s}</button>
                ))}
              </div>
              {focusName && <p className="hint">{focusInProject ? "Using the focused graph node" : "Using the most imported file"}: <span className="mono">{focusName}</span>.</p>}
            </Panel>
          )}
          {turns.map((t, i) => {
            const error = t.error;
            return (
              <div key={i} className="turn reveal">
                <div className="turn__q"><span className="turn__who">You</span>{t.question}</div>
                {!t.answer && !t.error && (
                  <div className="turn__pending"><Spinner size={16} /> Retrieving from Neo4j and asking the model…</div>
                )}
                {t.answer?.kind === "clarification" && (
                  <div className="answer">
                    <Notice tone="info" title="CodeAtlas needs a clearer target">{t.answer.answer}</Notice>
                    <p className="hint">The LLM was not called.</p>
                  </div>
                )}
                {t.answer && t.answer.kind !== "clarification" && (
                  <div className="answer">
                    <div className="answer__meta">
                      <Badge tone="copper">intent · {t.answer.intent}</Badge>
                      {t.answer.entity && <Badge mono>{t.answer.entity.caption}</Badge>}
                      <span className="muted small">written by the LLM from {t.answer.facts.length} graph facts</span>
                    </div>
                    <div className="answer__body"><Prose text={t.answer.answer} /></div>
                    {t.answer.caveats.length > 0 && (
                      <Collapsible title={`Caveats (${t.answer.caveats.length})`}>
                        <ul className="plain-list">{t.answer.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
                      </Collapsible>
                    )}
                    <Evidence answer={t.answer} idx={i} />
                  </div>
                )}
                {error != null && (error instanceof ApiError && error.status !== 0 ? <LlmFailure err={error} idx={i} /> : <ErrorState error={error} />)}
              </div>
            );
          })}
        </div>

        <form className="qa__composer" onSubmit={(e) => { e.preventDefault(); void submit(question); }}>
          {focusInProject && (
            <div className="qa__focus">Focus: <span className="badge mono">{focusInProject.label} · {focusInProject.caption}</span></div>
          )}
          <div className="qa__input">
            <textarea
              className="input textarea"
              rows={2}
              maxLength={500}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit(question);
                }
              }}
              placeholder="Ask about this repository… (Enter to send, Shift+Enter for a new line)"
              aria-label="Question"
            />
            <button className="btn btn--primary" type="submit" disabled={asking || question.trim().length < 3}>
              {asking ? <Spinner size={14} /> : "Ask"}
            </button>
          </div>
          <p className="hint">Each question makes one request to the configured LLM through the CodeAtlas API.</p>
        </form>
      </div>
    </>
  );
}
