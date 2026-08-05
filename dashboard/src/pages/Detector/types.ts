/**
 * What a detector page reads off the wire.
 *
 * These mirror the exporter's `v = 1` payloads (`pipeline/src/observatory/
 * export/`) the same way `src/data/types.ts` does — field names are the
 * contract. Two things are deliberate:
 *
 * - Every field a *small* export may legitimately omit is optional here, not
 *   asserted. A digit stratum that was skipped carries NO `p` key at all, and
 *   a page that read `p` off it and printed `0` would be inventing a result.
 * - The narrowing functions below are the only way into these types. JSON is
 *   version-gated by `decodeVersioned` and otherwise trusted, so the shape has
 *   to be checked where it is used rather than assumed.
 */

import type { JsonValue, RaceSummary, ScoreDetail } from "../../data/types";

/** One row of `anomalies/{key}.json` — a section, ranked. */
export interface AnomalyRow {
  readonly mun_code: string;
  readonly mun_name: string;
  readonly uf: string;
  readonly zone: number;
  readonly section: number;
  readonly race: string;
  readonly round: number;
  readonly race_id: string;
  readonly score: number;
  readonly detail: ScoreDetail;
}

/** `anomalies/{key}.json` — the national top-N for one detector. */
export interface AnomaliesFile {
  readonly v: number;
  readonly detector: string;
  readonly key: string;
  readonly top_n: number;
  readonly rows_scored: number;
  readonly flag_threshold: number | null;
  readonly rows: readonly JsonValue[];
}

/** One BH family of `anomalies/clustering.json`. */
export interface ClusteringTest {
  readonly high: number;
  readonly p_cutoff: number;
  readonly sections: number;
  readonly surviving: number;
  readonly groups_tested: number;
}

/** `anomalies/clustering.json` — concentration of the composite's high set. */
export interface ClusteringFile {
  readonly v: number;
  readonly computed: boolean;
  readonly q: number;
  readonly null_model: string;
  readonly tests: { readonly [family: string]: ClusteringTest };
  readonly levels: { readonly [level: string]: readonly JsonValue[] };
}

/** One state × size-bucket × test cell of the digit screen. */
export interface DigitStratum {
  readonly uf: string;
  readonly test: string;
  readonly bucket: string;
  readonly n: number;
  readonly skipped: boolean;
  /** Present only when `skipped` — why the cell carries no test. */
  readonly reason?: string;
  /** Absent on a skipped stratum. Never NaN, never a stand-in zero. */
  readonly p?: number;
  readonly chi2?: number;
  readonly dof?: number;
  readonly digit_freqs?: readonly number[];
  readonly digit_counts?: readonly number[];
  readonly expected_freqs?: readonly number[];
}

export interface DigitsPayload {
  readonly uf: string;
  readonly strata: readonly DigitStratum[];
  readonly buckets: readonly string[];
  readonly n_counts: number;
}

/** One round of the Klimek fingerprint: `grid[turnoutBin][shareBin]`. */
export interface FingerprintRound {
  readonly grid: readonly (readonly number[])[];
  readonly round: number;
  readonly candidate: number;
  readonly candidate_name: string;
  readonly n_sections: number;
  readonly corner_mass: number;
  readonly corner_sections: number;
}

export interface FingerprintPayload extends FingerprintRound {
  readonly bins: number;
  readonly rounds?: { readonly [round: string]: FingerprintRound };
}

/** The urna-model contrast: the raw gap, and the same gap within municipality. */
export interface UrnaModelPayload {
  readonly uf: string;
  readonly raw_gap: number;
  readonly within_mun_gap: number;
  readonly ci95: readonly [number, number];
  readonly se: number;
  readonly n_municipalities: number;
  readonly mixed_votes: number;
  readonly model_sections: { readonly [model: string]: number };
  readonly shares: { readonly [model: string]: number };
  readonly votes: { readonly [model: string]: { readonly valid: number; readonly subject: number } };
}

/** `states/{UF}.json` — per-state totals plus the distribution payloads. */
export interface StateAggregate {
  readonly v: number;
  readonly uf: string;
  readonly sections: number;
  readonly races: { readonly [race: string]: RaceSummary };
  readonly detectors: { readonly [key: string]: { readonly [field: string]: JsonValue } };
  readonly payloads?: { readonly [key: string]: JsonValue };
}

function isRecord(value: unknown): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberAt(record: Record<string, JsonValue>, field: string): number | null {
  const value = record[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function numberArray(value: JsonValue | undefined): readonly number[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((entry) => typeof entry === "number") ? (value as readonly number[]) : null;
}

/**
 * Keep the rows this build can actually rank.
 *
 * A row without a finite score cannot be placed in a ranking at all — sorting
 * it puts it somewhere arbitrary and rendering it prints a blank cell in a
 * numbered list. Dropping it is the only honest option, and it keeps the
 * `data-score` attribute a number by construction.
 */
export function anomalyRows(file: AnomaliesFile | undefined): readonly AnomalyRow[] {
  if (!file || !Array.isArray(file.rows)) return [];
  const rows: AnomalyRow[] = [];
  for (const raw of file.rows) {
    if (!isRecord(raw)) continue;
    const score = numberAt(raw, "score");
    const code = raw["mun_code"];
    if (score === null || typeof code !== "string") continue;
    const detail = raw["detail"];
    rows.push({
      mun_code: code,
      mun_name: typeof raw["mun_name"] === "string" ? raw["mun_name"] : code,
      uf: typeof raw["uf"] === "string" ? raw["uf"] : "",
      zone: numberAt(raw, "zone") ?? 0,
      section: numberAt(raw, "section") ?? 0,
      race: typeof raw["race"] === "string" ? raw["race"] : "",
      round: numberAt(raw, "round") ?? 0,
      race_id: typeof raw["race_id"] === "string" ? raw["race_id"] : "",
      score,
      detail: isRecord(detail) ? detail : {},
    });
  }
  return rows;
}

/** The digit payload of a state file, or `null` when it carries none. */
export function digitsPayload(state: StateAggregate | undefined): DigitsPayload | null {
  const raw = state?.payloads?.["digits"];
  if (!isRecord(raw) || !Array.isArray(raw["strata"])) return null;
  const strata: DigitStratum[] = [];
  for (const entry of raw["strata"]) {
    if (!isRecord(entry)) continue;
    const skipped = entry["skipped"] === true;
    const p = numberAt(entry, "p");
    const chi2 = numberAt(entry, "chi2");
    const dof = numberAt(entry, "dof");
    const observed = numberArray(entry["digit_freqs"]);
    const counts = numberArray(entry["digit_counts"]);
    const expected = numberArray(entry["expected_freqs"]);
    const reason = entry["reason"];
    strata.push({
      uf: typeof entry["uf"] === "string" ? entry["uf"] : (state?.uf ?? ""),
      test: typeof entry["test"] === "string" ? entry["test"] : "",
      bucket: typeof entry["bucket"] === "string" ? entry["bucket"] : "",
      n: numberAt(entry, "n") ?? 0,
      skipped,
      ...(typeof reason === "string" ? { reason } : {}),
      ...(p === null ? {} : { p }),
      ...(chi2 === null ? {} : { chi2 }),
      ...(dof === null ? {} : { dof }),
      ...(observed === null ? {} : { digit_freqs: observed }),
      ...(counts === null ? {} : { digit_counts: counts }),
      ...(expected === null ? {} : { expected_freqs: expected }),
    });
  }
  const buckets = raw["buckets"];
  return {
    uf: typeof raw["uf"] === "string" ? raw["uf"] : (state?.uf ?? ""),
    strata,
    buckets: Array.isArray(buckets) ? buckets.filter((b): b is string => typeof b === "string") : [],
    n_counts: numberAt(raw, "n_counts") ?? 0,
  };
}

function fingerprintRound(raw: Record<string, JsonValue>, bins: number): FingerprintRound | null {
  const grid = raw["grid"];
  if (!Array.isArray(grid)) return null;
  const rows: (readonly number[])[] = [];
  for (const row of grid) {
    const values = numberArray(row);
    if (values === null) return null;
    rows.push(values);
  }
  if (rows.length === 0 || rows.length !== bins) return null;
  return {
    grid: rows,
    round: numberAt(raw, "round") ?? 0,
    candidate: numberAt(raw, "candidate") ?? 0,
    candidate_name: typeof raw["candidate_name"] === "string" ? raw["candidate_name"] : "",
    n_sections: numberAt(raw, "n_sections") ?? 0,
    corner_mass: numberAt(raw, "corner_mass") ?? 0,
    corner_sections: numberAt(raw, "corner_sections") ?? 0,
  };
}

/** The fingerprint payload of a state file, or `null` when it carries none. */
export function fingerprintPayload(state: StateAggregate | undefined): FingerprintPayload | null {
  const raw = state?.payloads?.["fingerprint"];
  if (!isRecord(raw)) return null;
  const grid = raw["grid"];
  const bins = numberAt(raw, "bins") ?? (Array.isArray(grid) ? grid.length : 0);
  const top = fingerprintRound(raw, bins);
  if (!top || bins <= 0) return null;

  const rawRounds = raw["rounds"];
  const rounds: Record<string, FingerprintRound> = {};
  if (isRecord(rawRounds))
    for (const [round, entry] of Object.entries(rawRounds)) {
      if (!isRecord(entry)) continue;
      const decoded = fingerprintRound(entry, bins);
      if (decoded) rounds[round] = decoded;
    }

  return {
    ...top,
    bins,
    ...(Object.keys(rounds).length > 0 ? { rounds } : {}),
  };
}

/** The urna-model payload of a state file, or `null` when it carries none. */
export function urnaModelPayload(state: StateAggregate | undefined): UrnaModelPayload | null {
  const raw = state?.payloads?.["urna_model"];
  if (!isRecord(raw)) return null;
  const rawGap = numberAt(raw, "raw_gap");
  const withinGap = numberAt(raw, "within_mun_gap");
  if (rawGap === null || withinGap === null) return null;

  const ci = numberArray(raw["ci95"]);
  const numbersOf = (field: string): Record<string, number> => {
    const record = raw[field];
    if (!isRecord(record)) return {};
    return Object.fromEntries(
      Object.entries(record).filter((entry): entry is [string, number] => typeof entry[1] === "number"),
    );
  };

  const votesRaw = raw["votes"];
  const votes: Record<string, { valid: number; subject: number }> = {};
  if (isRecord(votesRaw))
    for (const [model, entry] of Object.entries(votesRaw)) {
      if (!isRecord(entry)) continue;
      const valid = numberAt(entry, "valid");
      const subject = numberAt(entry, "subject");
      if (valid !== null && subject !== null) votes[model] = { valid, subject };
    }

  return {
    uf: typeof raw["uf"] === "string" ? raw["uf"] : (state?.uf ?? ""),
    raw_gap: rawGap,
    within_mun_gap: withinGap,
    ci95: ci !== null && ci.length === 2 ? [ci[0] as number, ci[1] as number] : [withinGap, withinGap],
    se: numberAt(raw, "se") ?? 0,
    n_municipalities: numberAt(raw, "n_municipalities") ?? 0,
    mixed_votes: numberAt(raw, "mixed_votes") ?? 0,
    model_sections: numbersOf("model_sections"),
    shares: numbersOf("shares"),
    votes,
  };
}

/**
 * `anomalies/clustering.json`, narrowed.
 *
 * `computed: false` is a real state the exporter writes (the composite never
 * ran, so there is no high set to test for concentration) and it must not read
 * as "tested, nothing found" — the caller gets `null` and says so.
 */
export function clusteringResult(raw: unknown): ClusteringFile | null {
  if (!isRecord(raw) || raw["computed"] !== true) return null;
  const record = raw;

  const tests: Record<string, ClusteringTest> = {};
  const rawTests = record["tests"];
  if (isRecord(rawTests))
    for (const [family, entry] of Object.entries(rawTests)) {
      if (!isRecord(entry)) continue;
      tests[family] = {
        high: numberAt(entry, "high") ?? 0,
        p_cutoff: numberAt(entry, "p_cutoff") ?? 0,
        sections: numberAt(entry, "sections") ?? 0,
        surviving: numberAt(entry, "surviving") ?? 0,
        groups_tested: numberAt(entry, "groups_tested") ?? 0,
      };
    }

  const levels: Record<string, readonly JsonValue[]> = {};
  const rawLevels = record["levels"];
  if (isRecord(rawLevels))
    for (const [level, entry] of Object.entries(rawLevels))
      if (Array.isArray(entry)) levels[level] = entry;

  return {
    v: numberAt(record, "v") ?? 1,
    computed: true,
    q: numberAt(record, "q") ?? 0,
    null_model: typeof record["null_model"] === "string" ? record["null_model"] : "",
    tests,
    levels,
  };
}
