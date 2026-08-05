/**
 * The detector registry: one entry per `/detectors/:id`, and the only place
 * that knows which files a detector page reads and which blocks it renders.
 *
 * Why a registry rather than nine pages: the detectors differ in *what they
 * measured*, not in how a reader should be walked through it. Every page owes
 * the same things — what this test is, what it found, what it cannot tell you,
 * and where to look next — and pulling that into one shell means a new
 * detector is an entry here plus a block, not a new page that quietly forgets
 * the caveat.
 *
 * Two distinctions the registry has to carry, because getting either wrong
 * misleads the reader:
 *
 * - **`kind`.** A `section-score` detector scored every section and its
 *   summary counters mean something. A `distribution` detector scores nothing
 *   (`sections_scored = 0` by construction) and delivers a state-level
 *   payload; printing its zero as "0 sections flagged" would report a clean
 *   bill of health from a test that does not work that way.
 * - **`caveatFirst`.** The digit screen's caveat has to be read BEFORE its
 *   chi-squares, not under them. That is a stance requirement, not a layout
 *   preference, so it lives in the contract rather than in a component.
 */

import type { ScoreKey } from "../../data/types";
import type { MessageKey } from "../../i18n";

/** Every detector this build ships a page for. */
export const DETECTOR_IDS = [
  "composite",
  "extreme_dominance",
  "split_ticket",
  "turnout",
  "t1_arithmetic",
  "t1_presence",
  "fingerprint",
  "digits",
  "urna_model",
] as const;

export type DetectorId = (typeof DETECTOR_IDS)[number];

/** A rendered result block. Order in `blocks` is the order on the page. */
export type BlockId =
  | "ranking"
  | "distribution"
  | "clustering"
  | "fingerprint"
  | "digits"
  | "urna-model";

/** Which `states/{UF}.json` payload a distribution detector reads. */
export type PayloadId = "digits" | "fingerprint" | "urna_model";

/**
 * One detector-specific column of the ranking, read out of the row's `detail`.
 *
 * `labelKey` is explicit rather than derived so a field the drill-down already
 * has copy for (`k`, `n`, `p_hat`) is translated once, in one namespace.
 */
export interface ContextColumn {
  readonly field: string;
  readonly labelKey: MessageKey;
}

export interface DetectorDefinition {
  readonly id: DetectorId;
  /** Wire ids are snake_case; message keys hyphenate inside a segment. */
  readonly slug: string;
  readonly kind: "section-score" | "distribution";
  readonly blocks: readonly BlockId[];
  /** `anomalies/{scoreKey}.json`, for the ranking and the distribution. */
  readonly scoreKey?: ScoreKey;
  /** The `states/{UF}.json` payload this page reads, if any. */
  readonly payload?: PayloadId;
  readonly columns: readonly ContextColumn[];
  readonly caveatFirst: boolean;
}

function slugOf(id: string): string {
  return id.replace(/_/g, "-");
}

function scored(
  id: DetectorId,
  columns: readonly ContextColumn[],
  extra?: readonly BlockId[],
): DetectorDefinition {
  return {
    id,
    slug: slugOf(id),
    kind: "section-score",
    blocks: ["ranking", "distribution", ...(extra ?? [])],
    scoreKey: id,
    columns,
    caveatFirst: false,
  };
}

function distribution(
  id: DetectorId,
  payload: PayloadId,
  block: BlockId,
  caveatFirst = false,
): DetectorDefinition {
  return {
    id,
    slug: slugOf(id),
    kind: "distribution",
    blocks: [block],
    payload,
    columns: [],
    caveatFirst,
  };
}

const ENTRIES: readonly DetectorDefinition[] = [
  scored(
    "composite",
    [
      { field: "n_detectors", labelKey: "detector.column.n-detectors" },
      { field: "composite_percentile", labelKey: "detector.column.percentile" },
    ],
    ["clustering"],
  ),
  scored("extreme_dominance", [
    { field: "cand", labelKey: "municipality.detail.cand" },
    { field: "k", labelKey: "municipality.detail.k" },
    { field: "n", labelKey: "municipality.detail.n" },
    { field: "p_hat", labelKey: "municipality.detail.p-hat" },
  ]),
  scored("split_ticket", [
    { field: "cand", labelKey: "municipality.detail.cand" },
    { field: "d", labelKey: "detector.column.d" },
    { field: "median", labelKey: "detector.column.median" },
  ]),
  scored("turnout", [
    { field: "t", labelKey: "detector.column.t" },
    { field: "median", labelKey: "detector.column.median" },
  ]),
  scored("t1_arithmetic", [
    { field: "nominal", labelKey: "detector.column.nominal" },
    { field: "votes_total", labelKey: "detector.column.votes-total" },
    { field: "comparecimento", labelKey: "detector.column.comparecimento" },
  ]),
  scored("t1_presence", [{ field: "missing", labelKey: "detector.column.missing" }]),
  distribution("fingerprint", "fingerprint", "fingerprint"),
  // The one page whose caveat is read before its numbers.
  distribution("digits", "digits", "digits", true),
  distribution("urna_model", "urna_model", "urna-model"),
];

export const REGISTRY: Readonly<Record<DetectorId, DetectorDefinition>> = Object.fromEntries(
  ENTRIES.map((entry) => [entry.id, entry]),
) as Record<DetectorId, DetectorDefinition>;

/** True for a route id this build has a detector page for. */
export function isDetectorId(value: string | undefined): value is DetectorId {
  return value !== undefined && (DETECTOR_IDS as readonly string[]).includes(value);
}

/** The registry entry for a route id, or `null` when there is none. */
export function detectorFor(id: string | undefined): DetectorDefinition | null {
  return isDetectorId(id) ? REGISTRY[id] : null;
}

/** A detector's display name key — `extreme_dominance` → `…extreme-dominance…`. */
export function nameKey(definition: DetectorDefinition): MessageKey {
  return `detector.${definition.slug}.name`;
}
