/**
 * Locale-aware number formatting, shared by every page.
 *
 * These live above `pages/` because the drill-down and the overview must round
 * and separate numbers identically — a share that reads `72,7 %` on the map and
 * `72.7%` in the table is the kind of inconsistency a reader notices before any
 * of the statistics.
 *
 * The one rule worth stating: **a share is never rendered raw.** `null` means
 * "no denominator", and it prints as an em dash rather than `0 %`.
 */

/** `0.7269` → `72,7 %`; `null` → `—`. */
export function formatShare(locale: string, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value);
}

/** Thousands-separated integer. */
export function formatCount(locale: string, value: number): string {
  return new Intl.NumberFormat(locale).format(value);
}

/** A detector score, always two decimals so badges line up. */
export function formatScore(locale: string, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}
