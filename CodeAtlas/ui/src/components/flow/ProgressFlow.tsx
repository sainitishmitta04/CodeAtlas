import type { CSSProperties, ReactNode } from "react";

export type StepState = "done" | "current" | "todo" | "error" | "skipped";

export interface FlowStep {
  key: string;
  label: string;
  hint?: string;
  state?: StepState;
}

interface Props {
  steps: FlowStep[];
  /** Index of the active step (-1: none active, e.g. a workflow not started). */
  current: number;
  /** Clicking a step marker jumps to it (tours); omit for non-navigable workflows. */
  onSelect?: (index: number) => void;
  /** Overall completion (e.g. the last tour step has been reached). */
  complete?: boolean;
  ariaLabel: string;
  /** Show "Step x of y · z%". */
  showCount?: boolean;
  /** Rendered under the track (e.g. truncation warning). */
  footer?: ReactNode;
  /** Rendered at the right of the header (e.g. Previous / Next). */
  controls?: ReactNode;
  /** Maximum labelled steps before switching to compact markers. */
  maxLabels?: number;
}

/** Ordered-process indicator: a filled track with step markers, the active step and completion. */
export function ProgressFlow({ steps, current, onSelect, complete, ariaLabel, showCount = true, footer, controls, maxLabels = 6 }: Props) {
  const total = steps.length;
  if (!total) return null;
  const pos = Math.max(-1, Math.min(current, total - 1));
  const fraction = complete ? 1 : total === 1 ? (pos >= 0 ? 1 : 0) : Math.max(0, pos) / (total - 1);
  const percent = complete ? 100 : Math.round(((pos + 1) / total) * 100);
  const compact = total > maxLabels;
  const stateOf = (i: number): StepState =>
    steps[i].state ?? (complete || i < pos ? "done" : i === pos ? "current" : "todo");

  return (
    <div className={`flow${compact ? " flow--compact" : ""}${complete ? " is-complete" : ""}`}>
      {(showCount || controls) && (
        <div className="flow__head">
          {showCount && (
            <div className="flow__count" aria-live="polite">
              <span className="flow__step">
                {pos >= 0 ? <>Step <b>{pos + 1}</b> of {total}</> : <>{total} steps</>}
              </span>
              <span className="flow__pct mono">{percent}%</span>
              {complete && <span className="flow__done">Complete</span>}
            </div>
          )}
          {controls && <div className="flow__controls">{controls}</div>}
        </div>
      )}
      <div
        className="flow__track"
        role="progressbar"
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={complete ? total : pos + 1}
        aria-valuetext={pos >= 0 ? `Step ${pos + 1} of ${total}: ${steps[pos].label}` : `${total} steps`}
        style={{ "--fill": fraction } as CSSProperties}
      >
        <div className="flow__rail" aria-hidden="true"><div className="flow__fill" /></div>
        <ol className="flow__steps">
          {steps.map((s, i) => {
            const st = stateOf(i);
            // Compact tracks label the active step, plus the ends when they are far enough away not to collide.
            const farFromCurrent = pos < 0 || Math.abs(i - pos) / (total - 1) > 0.22;
            const showLabel = !compact || i === pos || ((i === 0 || i === total - 1) && farFromCurrent);
            const left = total === 1 ? 50 : (i / (total - 1)) * 100;
            const content = (
              <>
                <span className="flow__dot" aria-hidden="true" />
                {showLabel && <span className="flow__label">{s.label}</span>}
              </>
            );
            return (
              <li
                key={s.key}
                className={`flow__item is-${st}${showLabel ? " has-label" : ""}${i === 0 ? " is-first" : ""}${i === total - 1 ? " is-last" : ""}`}
                style={{ "--x": `${left}%` } as CSSProperties}
                title={s.hint ? `${s.label} — ${s.hint}` : s.label}
              >
                {onSelect ? (
                  <button
                    type="button"
                    className="flow__btn"
                    onClick={() => onSelect(i)}
                    aria-current={i === pos ? "step" : undefined}
                    aria-label={`Step ${i + 1}: ${s.label}${st === "done" ? " (visited)" : ""}`}
                  >
                    {content}
                  </button>
                ) : (
                  <span className="flow__btn" aria-current={i === pos ? "step" : undefined}>
                    {content}
                    <span className="sr-only"> ({st})</span>
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </div>
      {footer && <div className="flow__footer">{footer}</div>}
    </div>
  );
}
