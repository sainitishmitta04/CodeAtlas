import type { CSSProperties, ReactNode } from "react";

export function PageHeader({ eyebrow, title, children, actions }: { eyebrow?: ReactNode; title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head reveal">
      <div className="page-head__text">
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1 className="page-title">{title}</h1>
        {children && <p className="page-lede">{children}</p>}
      </div>
      {actions && <div className="page-head__actions">{actions}</div>}
    </header>
  );
}

export function Section({ title, kicker, children, actions, className = "", index = 0 }: { title?: ReactNode; kicker?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string; index?: number }) {
  return (
    <section className={`section reveal ${className}`} style={{ "--i": index } as CSSProperties}>
      {(title || actions) && (
        <div className="section__head">
          <div>
            {kicker && <div className="eyebrow">{kicker}</div>}
            {title && <h2 className="section__title">{title}</h2>}
          </div>
          {actions && <div className="section__actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Panel({ children, className = "", tone, style }: { children: ReactNode; className?: string; tone?: "paper" | "glass" | "flat"; style?: CSSProperties }) {
  return <div className={`panel${tone ? ` panel--${tone}` : ""} ${className}`} style={style}>{children}</div>;
}

export function Metric({ label, value, hint, accent }: { label: ReactNode; value: ReactNode; hint?: ReactNode; accent?: "sage" | "copper" | "warn" | "danger" }) {
  return (
    <div className={`metric${accent ? ` metric--${accent}` : ""}`}>
      <div className="metric__label">{label}</div>
      <div className="metric__value">{value}</div>
      {hint && <div className="metric__hint">{hint}</div>}
    </div>
  );
}
