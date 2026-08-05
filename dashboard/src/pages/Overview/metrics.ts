/**
 * What the map is coloured by, and how a number becomes a colour.
 *
 * Two rules drive everything here:
 *
 * 1. **`null` is not zero.** `flag_density` is `null` for every municipality
 *    until the composite detector has run, and a null column must produce an
 *    "available after full analysis" state — never a map painted in the
 *    lowest bin, which would say "we looked and found nothing". `Layer.state`
 *    is what carries that distinction to the UI.
 * 2. **Six bins, one hue.** Magnitude is a sequential encoding, so it gets one
 *    hue stepped light→dark (here dark→light, because the surface is dark) and
 *    never a rainbow. A binned scale rather than a continuous one, because a
 *    reader can match a polygon to a legend swatch and cannot match it to a
 *    gradient.
 */

import type { MunicipalitiesIndex } from "../../data/types";

/**
 * The sequential ramp, low → high, stepped for the `slate-950` page surface.
 *
 * Blue (the reference palette's default sequential hue) rather than a red/amber
 * "alarm" ramp: this site's whole stance is that a statistical signal is not an
 * accusation, and a map that goes red at the top would make the argument the
 * copy is careful not to make.
 *
 * Validated as an ordinal ramp against `#020617`: monotone lightness, every
 * adjacent gap ≥ 0.06 L, hue spread 4°, low end 2.49:1 on the surface.
 */
export const RAMP = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"] as const;

/** Municipality in this export, but the metric has no value for it. */
export const NO_VALUE_FILL = "#1e293b";

/** Geometry for a municipality this export does not cover at all. */
export const OUT_OF_SCOPE_FILL = "#0f172a";

export const METRIC_IDS = ["leading-share", "turnout", "flag-density"] as const;
export type MetricId = (typeof METRIC_IDS)[number];

export interface Metric {
  readonly id: MetricId;
  /** Per-municipality value, `null` where the export has none. */
  readonly extract: (index: MunicipalitiesIndex, i: number) => number | null;
  /** Bin edges — 5 cut points, so 6 bins. */
  readonly thresholds: (values: readonly number[]) => readonly number[];
  /**
   * True when the lowest bin is exactly zero rather than a range.
   *
   * Quantile edges start at the smallest POSITIVE value, so bin 0 of a flag
   * count holds the municipalities where the detectors found nothing — which
   * the legend must say in words. "< 0,1 %" would be technically true and
   * completely unreadable.
   */
  readonly zeroIsOwnBin: boolean;
}

/**
 * Sorted-array quantile (linear interpolation), used for skewed columns.
 */
function quantile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  const position = (sorted.length - 1) * p;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  const low = sorted[lower] as number;
  if (lower === upper) return low;
  return low + (position - lower) * ((sorted[upper] as number) - low);
}

/** Cut points at `probs` over `sorted`, de-duplicated upward. */
function edgesAt(sorted: readonly number[], probs: readonly number[]): readonly number[] {
  if (sorted.length === 0) return [];
  const edges: number[] = [];
  for (const p of probs) {
    const edge = quantile(sorted, p);
    if (edges.length === 0 || edge > (edges[edges.length - 1] as number)) edges.push(edge);
  }
  return edges;
}

/**
 * Five quantile cut points over the values, de-duplicated upward.
 *
 * Flag density is dominated by municipalities with nothing flagged, so fixed
 * linear bins would put ~everything in bin 0 and waste the whole ramp. Ties
 * (a column that is mostly the same number) collapse to fewer distinct edges;
 * `binOf` handles a short edge list, it just yields fewer used bins.
 */
function quantileThresholds(values: readonly number[]): readonly number[] {
  return edgesAt(
    values.filter((value) => value > 0).sort((a, b) => a - b),
    [0, 0.25, 0.5, 0.75, 0.9],
  );
}

/**
 * Six equal-count bins: cut points at every sixth of the distribution.
 *
 * Turnout is the case fixed bins cannot serve. It is a share, so anchored
 * 0–100 % edges are the obvious choice and the wrong one: ~every Brazilian
 * municipality lands between 70 % and 90 %, so the whole map would be painted
 * in one or two bins and the ramp would carry no information at all. Quantiles
 * over the column spend all six colours on the range the data actually
 * occupies, which is what makes "this municipality turned out unusually low
 * FOR BRAZIL" visible.
 *
 * The cost is that the ramp is relative to the export — a colour means
 * "in the bottom sixth of this export", not a fixed percentage — which is
 * exactly why the legend prints the numeric edges rather than a bare gradient,
 * and why the tooltip always shows the municipality's own value.
 *
 * `flag-density`'s edges start at the smallest POSITIVE value because zero is
 * its own meaningful bin there; turnout has no such floor, so it bins over
 * every value including the lowest.
 */
function evenQuantileThresholds(values: readonly number[]): readonly number[] {
  return edgesAt([...values].sort((a, b) => a - b), [1 / 6, 2 / 6, 0.5, 4 / 6, 5 / 6]);
}

/** The leading candidate's share of the headline race, per municipality. */
function leadingShare(index: MunicipalitiesIndex, i: number): number | null {
  const shares = index.headline_shares[i];
  if (!shares || shares.length === 0) return null;
  let best = Number.NEGATIVE_INFINITY;
  for (const share of shares) if (Number.isFinite(share) && share > best) best = share;
  return Number.isFinite(best) ? best : null;
}

/**
 * Flagged sections as a FRACTION of the municipality's sections.
 *
 * The export's `flag_density` column is a count, and a count is not comparable
 * across municipalities — Boa Vista's 8 flags over 741 sections is a quieter
 * result than São Luiz's 1 over 22. The map divides; the tooltip still shows
 * the raw count and the denominator, so nothing is hidden behind the ratio.
 */
function flagDensity(index: MunicipalitiesIndex, i: number): number | null {
  const flagged = index.flag_density[i];
  if (flagged === null || flagged === undefined) return null;
  const sections = index.sections[i] ?? 0;
  if (sections <= 0) return null;
  return flagged / sections;
}

/**
 * Turnout of the headline race, as the export computed it.
 *
 * Read straight off the column and never re-derived here: the exporter divides
 * summed `comparecimento` by summed `aptos` over the municipality's sections,
 * so the map, the state pages and `summary.json` all quote the same statistic.
 * `null` means there was no denominator, never "nobody voted".
 */
function turnout(index: MunicipalitiesIndex, i: number): number | null {
  return index.turnout[i] ?? null;
}

export const METRICS: { readonly [id in MetricId]: Metric } = {
  "leading-share": {
    id: "leading-share",
    extract: leadingShare,
    // Anchored, not data-driven: 50/60/70/80/90 % are the numbers a reader
    // already has intuitions about, and they keep the map comparable between
    // one export and the next.
    thresholds: () => [0.5, 0.6, 0.7, 0.8, 0.9],
    zeroIsOwnBin: false,
  },
  turnout: {
    id: "turnout",
    extract: turnout,
    thresholds: evenQuantileThresholds,
    zeroIsOwnBin: false,
  },
  "flag-density": {
    id: "flag-density",
    extract: flagDensity,
    thresholds: quantileThresholds,
    zeroIsOwnBin: true,
  },
};

/**
 * Whether a metric can be drawn at all.
 *
 * `not-computed` is the case the plan calls out: the column exists and is
 * entirely `null`, which means the analysis behind it has not run. It is NOT
 * `empty`, which would mean it ran and found nothing.
 */
export type LayerState = "ready" | "not-computed";

export interface Layer {
  readonly metric: Metric;
  readonly state: LayerState;
  /** Municipality code → value. Absent key = no value for that municipality. */
  readonly values: ReadonlyMap<string, number>;
  /** Bin edges; empty when nothing can be binned. */
  readonly thresholds: readonly number[];
}

/** Build the colour layer for one metric over the municipality index. */
export function buildLayer(metric: Metric, index: MunicipalitiesIndex): Layer {
  const values = new Map<string, number>();
  for (let i = 0; i < index.code.length; i += 1) {
    const value = metric.extract(index, i);
    if (value === null || !Number.isFinite(value)) continue;
    values.set(index.code[i] as string, value);
  }
  return {
    metric,
    state: values.size === 0 ? "not-computed" : "ready",
    values,
    thresholds: metric.thresholds([...values.values()]),
  };
}

/** Which of the six bins a value falls in. `edges` may be shorter than 5. */
export function binOf(value: number, edges: readonly number[]): number {
  let bin = 0;
  for (const edge of edges) {
    if (value < edge) break;
    bin += 1;
  }
  return Math.min(bin, RAMP.length - 1);
}

/** The fill for a value under a layer. */
export function fillOf(layer: Layer, value: number | undefined): string {
  if (value === undefined) return NO_VALUE_FILL;
  return RAMP[binOf(value, layer.thresholds)] as string;
}
