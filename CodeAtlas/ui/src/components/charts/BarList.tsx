import type { CSSProperties } from "react";
import { fmtNumber, pct } from "../../lib/format";

export interface Bar { key: string; label: string; value: number; color?: string; hint?: string }

/** Horizontal distribution bars; widths are proportional to the largest value. */
export function BarList({ bars, total, ariaLabel }: { bars: Bar[]; total?: number; ariaLabel: string }) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  const all = total ?? bars.reduce((s, b) => s + b.value, 0);
  return (
    <ul className="bars" aria-label={ariaLabel}>
      {bars.map((b, i) => (
        <li key={b.key} className="bars__row" style={{ "--i": i } as CSSProperties} title={b.hint}>
          <span className="bars__label">
            {b.color && <i className="bars__swatch" style={{ background: b.color }} />}
            {b.label}
          </span>
          <span className="bars__track" aria-hidden="true">
            <span className="bars__fill" style={{ width: `${(b.value / max) * 100}%`, background: b.color }} />
          </span>
          <span className="bars__value mono">
            {fmtNumber(b.value)}
            <small>{pct(b.value, all)}%</small>
          </span>
        </li>
      ))}
    </ul>
  );
}
