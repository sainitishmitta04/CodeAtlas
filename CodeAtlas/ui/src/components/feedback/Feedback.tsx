import { useId, useState, type ReactNode } from "react";
import { ApiError, componentName, errorText } from "../../lib/errors";
import type { Confidence } from "../../lib/types";

export type Tone = "info" | "warn" | "danger" | "ok" | "neutral";

export function Spinner({ label = "Loading", size = 18 }: { label?: string; size?: number }) {
  return (
    <span className="spinner" role="status" aria-label={label} style={{ width: size, height: size }} />
  );
}

export function Loading({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <div className="loading__head">
        <Spinner label={label} />
        <span>{label}</span>
      </div>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${92 - i * 14}%`, animationDelay: `${i * 120}ms` }} />
      ))}
    </div>
  );
}

export function Notice({ tone = "info", title, children, action }: { tone?: Tone; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={`notice notice--${tone}`} role={tone === "danger" ? "alert" : "note"}>
      <span className="notice__mark" aria-hidden="true" />
      <div className="notice__body">
        {title && <div className="notice__title">{title}</div>}
        {children && <div className="notice__text">{children}</div>}
      </div>
      {action && <div className="notice__action">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  const api = error instanceof ApiError ? error : null;
  const heading = title ?? (api?.status === 0 ? "Backend unavailable" : `${componentName(api?.component ?? null)} request failed`);
  return (
    <Notice
      tone="danger"
      title={heading}
      action={onRetry && <button className="btn btn--ghost btn--sm" onClick={onRetry}>Retry</button>}
    >
      {api ? (api.status === 0 ? api.message : `${api.status} · ${api.message}`) : errorText(error)}
    </Notice>
  );
}

export function EmptyState({ title, children, action, icon = "◌" }: { title: string; children?: ReactNode; action?: ReactNode; icon?: string }) {
  return (
    <div className="empty">
      <div className="empty__icon" aria-hidden="true">{icon}</div>
      <div className="empty__title">{title}</div>
      {children && <div className="empty__text">{children}</div>}
      {action && <div className="empty__action">{action}</div>}
    </div>
  );
}

export function Badge({ tone = "neutral", children, title, mono }: { tone?: Tone | "copper" | "sage"; children: ReactNode; title?: string; mono?: boolean }) {
  return <span className={`badge badge--${tone}${mono ? " mono" : ""}`} title={title}>{children}</span>;
}

const CONF_LEVEL: Record<Confidence, number> = { high: 3, medium: 2, low: 1, none: 0 };

export function ConfidenceMeter({ value, showLabel = true }: { value: Confidence; showLabel?: boolean }) {
  const level = CONF_LEVEL[value] ?? 0;
  return (
    <span className={`conf conf--${value}`} title={`${value} confidence`} aria-label={`${value} confidence`}>
      <span className="conf__bars" aria-hidden="true">
        {[1, 2, 3].map((i) => <i key={i} className={i <= level ? "on" : ""} />)}
      </span>
      {showLabel && <span className="conf__label">{value}</span>}
    </span>
  );
}

export function ObservedBadge({ observed }: { observed: boolean }) {
  return observed ? (
    <Badge tone="sage" title="Directly observed in the graph">observed</Badge>
  ) : (
    <Badge tone="warn" title="Inferred: not a directly observed relationship">inferred</Badge>
  );
}

/** Accessible disclosure with an animated body. */
export function Collapsible({ title, children, defaultOpen = false, meta }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; meta?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={`collapsible${open ? " is-open" : ""}`}>
      <button className="collapsible__head" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <span className="collapsible__chev" aria-hidden="true">›</span>
        <span className="collapsible__title">{title}</span>
        {meta && <span className="collapsible__meta">{meta}</span>}
      </button>
      <div className="collapsible__body" id={id} role="region" hidden={!open}>
        <div className="collapsible__inner">{open && children}</div>
      </div>
    </div>
  );
}

export function CypherBlock({ cypher, params }: { cypher: string[]; params?: Record<string, unknown> }) {
  if (!cypher.length) return null;
  return (
    <Collapsible title="Cypher used" meta={<span>run it in Aura Query to see the same graph</span>}>
      {cypher.map((q, i) => (
        <pre key={i} className="code code--block"><code>{q}</code></pre>
      ))}
      {params && Object.keys(params).length > 0 && (
        <pre className="code code--block"><code>{JSON.stringify(params, null, 2)}</code></pre>
      )}
    </Collapsible>
  );
}
