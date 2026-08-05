/**
 * The wire contract between the Python exporter and this dashboard.
 *
 * THESE TYPES MIRROR THE FROZEN `v = 1` SCHEMA (`pipeline/src/observatory/export/`).
 * Field names are the contract, not an implementation detail — `null_votes`
 * (not `nullVotes`), `comp` (not `comparecimento`), `score_detail`. Renaming
 * one here silently decouples the dashboard from the exporter, so don't.
 *
 * Race ids ALWAYS carry the round: `PRES_1`, never `PRES`. `ALL_1` / `ALL_2`
 * are reserved for cross-race detectors and appear only in `scores` /
 * `score_detail`, never in `races` / `votes` / `stats` / `cand`.
 */

/** Schema version this dashboard understands. Anything else is refused. */
export const SCHEMA_VERSION = 1;

/** Race codes the exporter emits, in ballot order (mirrors `RACE_ORDER`). */
export const RACE_CODES = ["PRES", "GOV", "SEN", "DEPFED", "DEPEST", "DEPDIS"] as const;
export type RaceCode = (typeof RACE_CODES)[number];

/** Rounds a race id may carry. */
export const ROUNDS = [1, 2] as const;
export type Round = (typeof ROUNDS)[number];

/** Reserved cross-race score ids (`race = 'ALL'` in the warehouse). */
export const CROSS_RACE_IDS = ["ALL_1", "ALL_2"] as const;
export type CrossRaceId = (typeof CROSS_RACE_IDS)[number];

/**
 * A race id such as `"PRES_1"` or `"DEPEST_1"`.
 *
 * Deliberately `string` rather than a template-literal union: consumers index
 * `Record<RaceId, …>` with values that arrive as plain strings (`Object.keys`,
 * route params, JSON), and a union would force casts at every boundary. The
 * decoder validates the shape at runtime instead — see `parseRaceId`.
 */
export type RaceId = string;

/** A `RaceId` or one of the reserved `CrossRaceId`s. Keys of `scores`. */
export type ScoreRaceId = string;

/** A detector's score key, e.g. `"extreme_dominance"`. */
export type ScoreKey = string;

/**
 * Raw bytes off the wire: an `ArrayBuffer` or any typed-array view over one.
 * Deliberately wider than the DOM's `BufferSource`, which pins the backing
 * buffer to a non-shared `ArrayBuffer` and rejects a plain `Uint8Array`.
 */
export type Bytes = ArrayBuffer | ArrayBufferView;

/** Anything `JSON.parse` can produce — used for detector-specific payloads. */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

/** `[number, name, party_number]` — the exporter's candidate tuple. */
export type CandidateEntry = readonly [number: number, name: string, party: number | null];

/**
 * Per-section turnout/ballot counters. Dense: one entry per section, aligned
 * with `Shard.zone` / `Shard.section` by index.
 */
export interface SectionStats {
  /** Registered voters (eleitores aptos). */
  readonly aptos: readonly number[];
  /** Turnout (comparecimento). */
  readonly comp: readonly number[];
  readonly blank: readonly number[];
  readonly null_votes: readonly number[];
  /** Party-list (legenda) votes — non-zero only for DEPFED/DEPEST. */
  readonly legenda: readonly number[];
}

/**
 * Candidate (nominal) votes as a sparse COO triple, sorted by `(sec, cand)`.
 * Zero pairs are omitted: no entry means no votes.
 */
export interface VotesCOO {
  /** Section position — an index into `Shard.zone` / `Shard.section`. */
  readonly sec: readonly number[];
  /** Candidate ballot number. */
  readonly cand: readonly number[];
  /** Vote count, always > 0. */
  readonly n: readonly number[];
}

/** Dense per-section detector output; `null` means that section was not scored. */
export type ScoreColumn = readonly (number | null)[];

/** Detector-specific context for one flagged section (baseline, n, tails…). */
export type ScoreDetail = { readonly [field: string]: JsonValue };

/** One municipality (or, when split, one zone of one municipality). */
export interface Shard {
  readonly v: number;
  /** TSE municipality code, zero-padded, e.g. `"03158"`. */
  readonly code: string;
  readonly name: string;
  readonly uf: string;
  /** Present only on a subshard of a split municipality. */
  readonly zone_only?: number;
  readonly races: readonly RaceId[];
  readonly cand: { readonly [race: RaceId]: readonly CandidateEntry[] };
  /** Parallel to `section`; sorted by `(zone, section)`. */
  readonly zone: readonly number[];
  readonly section: readonly number[];
  readonly stats: { readonly [race: RaceId]: SectionStats };
  readonly votes: { readonly [race: RaceId]: VotesCOO };
  readonly scores: { readonly [race: ScoreRaceId]: { readonly [key: ScoreKey]: ScoreColumn } };
  /** `race → section index (as a string) → score key → detail`. */
  readonly score_detail: {
    readonly [race: ScoreRaceId]: {
      readonly [sectionIndex: string]: { readonly [key: ScoreKey]: ScoreDetail };
    };
  };
}

/**
 * Written at `shards/{code}.json.gz` when a municipality exceeded the shard
 * budget: the real data lives in `shards/{code}-z{zone}.json.gz`.
 * `useShard` resolves this transparently — callers never see it.
 */
export interface SplitIndex {
  readonly v: number;
  readonly split: true;
  readonly code: string;
  readonly name: string;
  readonly uf: string;
  readonly zones: readonly number[];
}

/** What `shards/{code}.json.gz` may contain. */
export type ShardDocument = Shard | SplitIndex;

/** One entry of the content-hashed manifest. */
export interface ManifestEntry {
  /** Physical, content-hashed path relative to `data/`. */
  readonly path: string;
  readonly sha256: string;
  readonly bytes: number;
}

/**
 * `data/data-manifest.json` — the only file whose content changes under a
 * stable name, and the only way to turn a logical name into a URL.
 */
export interface Manifest {
  readonly v: number;
  /** ISO-8601 UTC timestamp of the export run. */
  readonly generated: string;
  /** Logical name → physical entry. Absent key = file not in this export. */
  readonly files: { readonly [logical: string]: ManifestEntry };
  readonly count: number;
  readonly bytes: number;
}

/** Per-race totals shared by `summary.json` and `states/{UF}.json`. */
export interface RaceSummary {
  readonly race: string;
  readonly round: number;
  readonly candidates: number;
  readonly total_votes: number;
  readonly legenda: number;
  readonly sections: number;
  readonly aptos: number;
  readonly comparecimento: number;
  readonly abstentions: number;
  readonly blank: number;
  readonly null_votes: number;
  readonly turnout: number;
}

export interface DetectorSummary {
  readonly detector: string;
  readonly sections_scored: number;
  readonly sections_flagged: number;
  readonly at: string;
}

/** `summary.json` — the eagerly-loaded national header. */
export interface Summary {
  readonly v: number;
  readonly ufs: readonly string[];
  readonly municipalities: number;
  readonly sections: number;
  readonly races: { readonly [race: RaceId]: RaceSummary };
  readonly detectors: { readonly [key: ScoreKey]: DetectorSummary };
  readonly annulled: {
    readonly candidates: number;
    readonly votes: number;
    readonly legenda_votes: number;
    readonly municipalities: number;
  };
}

/** `municipalities-index.json` — columnar, one entry per municipality. */
export interface MunicipalitiesIndex {
  readonly v: number;
  readonly code: readonly string[];
  readonly name: readonly string[];
  readonly uf: readonly string[];
  readonly lat: readonly (number | null)[];
  readonly lon: readonly (number | null)[];
  readonly sections: readonly number[];
  readonly headline: {
    readonly race_id: RaceId;
    readonly candidates: readonly { readonly number: number; readonly name: string }[];
  };
  readonly headline_shares: readonly (readonly number[] | null)[];
  readonly flag_density: readonly (number | null)[];
  /**
   * Vote-weighted turnout of the headline race, per municipality — the ratio of
   * the municipality's summed `comparecimento` to its summed `aptos`, `null`
   * where there is no denominator. Warehouse data, not a detector reading.
   */
  readonly turnout: readonly (number | null)[];
}

/** Split a race id into its parts. Returns `null` if it is not a race id. */
export function parseRaceId(id: string): { race: string; round: Round } | null {
  const match = /^([A-Z]+)_([12])$/.exec(id);
  if (!match) return null;
  const [, race, round] = match;
  if (race === undefined || round === undefined) return null;
  return { race, round: round === "1" ? 1 : 2 };
}

/** True for the reserved cross-race score ids. */
export function isCrossRaceId(id: string): id is CrossRaceId {
  return (CROSS_RACE_IDS as readonly string[]).includes(id);
}

/** Ballot order, mirroring the exporter's `_race_sort_key`. */
export function raceSortKey(id: RaceId): [number, string, number] {
  const parsed = parseRaceId(id);
  if (!parsed) return [RACE_CODES.length + 1, id, 0];
  const rank = (RACE_CODES as readonly string[]).indexOf(parsed.race);
  return [rank === -1 ? RACE_CODES.length : rank, parsed.race, parsed.round];
}

/** Compare two race ids in ballot order. */
export function compareRaceIds(a: RaceId, b: RaceId): number {
  const [ra, na, da] = raceSortKey(a);
  const [rb, nb, db] = raceSortKey(b);
  return ra - rb || na.localeCompare(nb) || da - db;
}
