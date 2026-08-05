/**
 * The chart theme, shared by every block so nine detectors look like one site.
 *
 * Colour rules, in the order they were decided:
 *
 * 1. **Magnitude is one hue, stepped.** The heatmap and the histogram draw
 *    from the same validated blue ramp the choropleth uses (`Overview/metrics`)
 *    — importing it rather than inventing a second blue is the whole reason
 *    the map and the charts read as one system.
 * 2. **Two marks that mean different things are separated by lightness, not by
 *    a second hue.** `ACCENT` vs `MUTED` and `HIGH` vs `LOW` both clear the
 *    CVD and normal-vision separation floors (validated: ΔE 18.0 and 29.3
 *    normal-vision, ≥ 17.3 under protan/deutan/tritan) while staying inside
 *    one hue family — no rainbow, no alarm red.
 * 3. **A reference is neutral.** The Benford/uniform expectation is drawn as a
 *    dashed light-grey line, never as a rival coloured series: it is the null
 *    hypothesis, not a second measurement.
 *
 * Identity is never carried by colour alone — every chart here is either a
 * single series, or labelled on the axis, or accompanied by the legend below.
 */

import type { ReactNode } from "react";

import { RAMP } from "../Overview/metrics";

/** Sequential ramp, low → high. Shared with the choropleth. */
export { RAMP };

/** The primary data colour. */
export const ACCENT = "#6da7ec";
/** A recessive mark: present, deliberately not the one to read first. */
export const MUTED = "#64748b";
/** Emphasis pair for a binary split of the SAME measure (above/below a cut). */
export const HIGH = "#9ec5f4";
export const LOW = "#256abf";
/** The null hypothesis / expected value. Neutral by construction. */
export const REFERENCE = "#cbd5e1";

/** Grid, axis and tick ink, matched to the slate surface. */
export const GRID = "#1e293b";
export const AXIS_INK = "#94a3b8";

/** Axis defaults — recessive, 11px, no axis line. */
export const AXIS = {
  stroke: GRID,
  tick: { fill: AXIS_INK, fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

/** Dark-surface tooltip chrome. */
export const TOOLTIP = {
  contentStyle: {
    background: "#0f172a",
    border: "1px solid #334155",
    borderRadius: "0.375rem",
    fontSize: "0.75rem",
    color: "#e2e8f0",
  },
  labelStyle: { color: "#94a3b8" },
  itemStyle: { color: "#e2e8f0" },
  cursor: { fill: "#ffffff10" },
} as const;

/**
 * Sequential bin of `value` within `[0, max]`, √-spaced.
 *
 * Section counts per heatmap cell are extremely skewed — one dense cell and a
 * long tail of ones — and a linear ramp would paint the entire picture in the
 * lowest step, which is the same as not drawing it.
 */
export function rampStep(value: number, max: number): string {
  if (max <= 0) return RAMP[0] as string;
  const scaled = Math.sqrt(value / max);
  const step = Math.min(RAMP.length - 1, Math.max(0, Math.ceil(scaled * RAMP.length) - 1));
  return RAMP[step] as string;
}

/** A colour key. Present whenever a chart uses more than one fill. */
export function ChartLegend({ items }: { items: readonly { color: string; label: string }[] }) {
  return (
    <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-2.5 w-2.5 shrink-0 rounded-sm ring-1 ring-inset ring-white/10"
            style={{ background: item.color }}
          />
          <span>{item.label}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * A chart's box.
 *
 * Fixed height, `min-w-0` so a flex/grid parent cannot let a chart push the
 * page into a horizontal scroll, and the figure caption stays outside the SVG
 * where a screen reader can reach it.
 */
export function Figure({
  height = 220,
  label,
  children,
}: {
  height?: number;
  label: string;
  children: ReactNode;
}) {
  return (
    <figure className="min-w-0" aria-label={label}>
      <div style={{ height }}>{children}</div>
    </figure>
  );
}
