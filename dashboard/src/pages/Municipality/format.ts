/**
 * Number and label formatting for the drill-down.
 *
 * Two rules the rest of the page relies on:
 *
 * - A share is never rendered raw. `null` means "no denominator" (a section
 *   with no votes for that race) and prints as an em dash, not as `0 %`.
 * - Detector detail arrives as an open `Record<string, JsonValue>`, because
 *   detectors are added over time. Anything this build has a label for gets
 *   one; anything else prints under its own field name rather than being
 *   dropped, so a new detector degrades to "readable" instead of "invisible".
 */

import { RACE_CODES, isCrossRaceId, parseRaceId, type JsonValue } from "../../data/types";
import { formatCount, formatScore, formatShare } from "../../format";
import type { MessageKey, TranslationParams } from "../../i18n";

export type Translate = (key: MessageKey, params?: TranslationParams) => string;

/** Detector keys this build ships copy for. Anything else falls back. */
const KNOWN_DETECTORS = new Set(["extreme_dominance", "t1_arithmetic", "t1_presence"]);

/** Detail fields this build ships copy for. Anything else prints its own name. */
const KNOWN_DETAIL_FIELDS = new Set([
  "cand",
  "k",
  "n",
  "p_hat",
  "rho_hat",
  "p_hi",
  "p_lo",
  "baseline",
]);

/** `PRES_1` → "Presidente — 1º turno"; `ALL_1` → the cross-race label. */
export function raceLabel(t: Translate, raceId: string): string {
  const round = isCrossRaceId(raceId) ? (raceId.endsWith("_2") ? 2 : 1) : parseRaceId(raceId)?.round;
  if (round === undefined) return raceId;
  const code = isCrossRaceId(raceId) ? "ALL" : (parseRaceId(raceId)?.race ?? raceId);
  const known = code === "ALL" || (RACE_CODES as readonly string[]).includes(code);
  const name = known ? t(`municipality.race.${code.toLowerCase()}`) : code;
  return t("municipality.race.round", { race: name, round });
}

/** Wire identifiers are snake_case; message keys hyphenate inside a segment. */
function slug(identifier: string): string {
  return identifier.replace(/_/g, "-");
}

/** A detector's display name — its key, humanised, when there is no copy yet. */
export function detectorLabel(t: Translate, scoreKey: string): string {
  return KNOWN_DETECTORS.has(scoreKey)
    ? t(`municipality.detector.${slug(scoreKey)}`)
    : scoreKey.replace(/_/g, " ");
}

/** A detail field's display name — the raw field name when there is no copy. */
export function detailFieldLabel(t: Translate, field: string): string {
  return KNOWN_DETAIL_FIELDS.has(field) ? t(`municipality.detail.${slug(field)}`) : field;
}

/**
 * Number formatting moved up to `src/format.ts` when the overview needed the
 * same rounding; re-exported here so this module stays the drill-down's single
 * formatting import.
 */
export { formatCount, formatScore, formatShare };

/**
 * One detail value, whatever the detector put there.
 *
 * Tail probabilities are routinely 1e-34, so anything that small goes to
 * exponent notation instead of rounding to a misleading `0,00`.
 */
export function formatDetailValue(t: Translate, locale: string, value: JsonValue): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "string") {
    if (value === "municipality" || value === "state") return t(`municipality.baseline.${value}`);
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return formatCount(locale, value);
    if (value !== 0 && Math.abs(value) < 1e-3) return value.toExponential(2);
    return new Intl.NumberFormat(locale, { maximumSignificantDigits: 4 }).format(value);
  }
  if (Array.isArray(value)) return value.map((entry) => formatDetailValue(t, locale, entry)).join(", ");
  return Object.entries(value)
    .map(([key, entry]) => `${key}: ${formatDetailValue(t, locale, entry)}`)
    .join(" · ");
}

/**
 * Signal strength bands, used for colour only.
 *
 * Deliberately three coarse bands rather than a continuous ramp: a score is a
 * statistical distance, and a gradient invites reading precision into it that
 * the number does not carry.
 */
export type ScoreBand = "none" | "low" | "elevated" | "high";

export function scoreBand(value: number | null): ScoreBand {
  if (value === null || !Number.isFinite(value)) return "none";
  if (value >= 2) return "high";
  if (value >= 1) return "elevated";
  return "low";
}
