/**
 * The two numbers the detector pages need that the shared formatter does not
 * have: a p-value and a signed gap.
 */

import { formatCount, formatScore, formatShare } from "../../format";

export { formatCount, formatScore, formatShare };

/**
 * A p-value.
 *
 * Chi-square p-values off these tests are routinely 1e-20, and rounding one to
 * `0,000` would turn "astronomically unlikely under the null" into something a
 * reader could mistake for a rounding artefact. Same rule as the drill-down's
 * detail panel, so the two never disagree about the same number.
 */
export function formatP(locale: string, p: number | undefined): string {
  if (p === undefined || !Number.isFinite(p)) return "—";
  if (p !== 0 && Math.abs(p) < 1e-3) return p.toExponential(2);
  return new Intl.NumberFormat(locale, { maximumSignificantDigits: 3 }).format(p);
}

/**
 * A difference of two shares, always signed.
 *
 * The urna-model gaps are differences, and an unsigned `17,3 %` next to
 * `11,0 %` would lose the one thing the reader has to see: which way each one
 * points, and that both point the same way.
 */
export function formatSignedShare(locale: string, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "percent",
    signDisplay: "exceptZero",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value);
}

/** A share in percentage POINTS, for an axis that is already labelled `%`. */
export function formatPercentPoints(locale: string, value: number): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  }).format(value * 100);
}
