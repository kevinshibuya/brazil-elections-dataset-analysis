/**
 * Everything the drill-down derives from a decoded shard, as pure functions.
 *
 * Two invariants drive the whole file:
 *
 * 1. `votes` is a SPARSE COO triple. A candidate missing from a section got
 *    zero votes there — never "unknown", and never a reason for an index to
 *    slide. Every column produced here is DENSE and aligned with
 *    `shard.section` by position, built by scattering the COO into a
 *    pre-filled array rather than by walking it in order.
 * 2. Shares are computed against the section's own nominal-vote total (the sum
 *    of that race's candidate votes in that section), which is the denominator
 *    the detectors used — see `extreme_dominance`'s `n`. Blank and null votes
 *    are deliberately outside it.
 */

import {
  compareRaceIds,
  isCrossRaceId,
  parseRaceId,
  type CandidateEntry,
  type RaceId,
  type ScoreDetail,
  type ScoreKey,
  type ScoreRaceId,
  type Shard,
} from "../../data/types";

/** A candidate plus its municipal total and its dense per-section column. */
export interface CandidateView {
  readonly number: number;
  readonly name: string;
  readonly party: number | null;
  /** Municipal total for the race. */
  readonly total: number;
  /** Dense, one entry per section — zero where the COO had no pair. */
  readonly column: readonly number[];
}

/** One row of the section table, already reduced to what the table renders. */
export interface SectionRow {
  /** Position in the shard's dense columns — the identity everything joins on. */
  readonly index: number;
  readonly zone: number;
  readonly section: number;
  readonly aptos: number;
  readonly comp: number;
  /** `comp / aptos`, or `null` when the section has no registered voters. */
  readonly turnout: number | null;
  /** Nominal (candidate) votes cast in this section for the lens race. */
  readonly nominal: number;
  /** Share of `nominal` per lens candidate, aligned with `RaceLens.candidates`. */
  readonly shares: readonly (number | null)[];
  /** Highest detector score in the lens scope, or `null` if nothing scored it. */
  readonly maxScore: number | null;
}

/** The race the table is currently reading, plus its derived columns. */
export interface RaceLens {
  readonly raceId: RaceId;
  /** Leading candidates, municipal totals descending. */
  readonly candidates: readonly CandidateView[];
  /** Score race ids in scope: the race itself plus its round's `ALL_r`. */
  readonly scoreIds: readonly ScoreRaceId[];
  readonly rows: readonly SectionRow[];
}

/** One detector's output for one section, in one race id. */
export interface DetectorEntry {
  readonly raceId: ScoreRaceId;
  readonly scoreKey: ScoreKey;
  readonly score: number;
  /** Present only at or above the detector's flag threshold. */
  readonly detail: ScoreDetail | null;
  /** Sample size behind this race id in this section. */
  readonly sample: SampleSize;
}

/** How many observations a score was computed over, and of what. */
export interface SampleSize {
  readonly n: number;
  /** `nominal` = candidate votes for one race; `voters` = turnout of the round. */
  readonly kind: "nominal" | "voters";
}

/** A section's own value next to the municipality's, excluding that section. */
export interface LocalBaseline {
  /** The section's value. */
  readonly value: number | null;
  /** The same quantity over the rest of the municipality (leave-one-out). */
  readonly baseline: number | null;
  /** Observations the section's value rests on. */
  readonly sample: SampleSize;
}

/** The cross-race score id covering `raceId`'s round, e.g. `PRES_1` → `ALL_1`. */
export function crossRaceIdFor(raceId: RaceId): ScoreRaceId | null {
  const parsed = parseRaceId(raceId);
  return parsed ? `ALL_${parsed.round}` : null;
}

/**
 * Race ids whose scores belong to `raceId`'s lens.
 *
 * A cross-race detector writes to `ALL_r`, not to a race — so looking only at
 * `raceId` would hide it from the table's score column. Its round's `ALL_r` is
 * therefore in scope, when the shard has one.
 */
export function lensScoreIds(shard: Shard, raceId: RaceId): ScoreRaceId[] {
  const ids: ScoreRaceId[] = [raceId];
  const cross = crossRaceIdFor(raceId);
  if (cross && shard.scores[cross]) ids.push(cross);
  return ids;
}

/** Score race ids present in the shard, in ballot order, `ALL_r` last. */
export function scoreRaceIds(shard: Shard): ScoreRaceId[] {
  return Object.keys(shard.scores).sort((a, b) => {
    const crossA = isCrossRaceId(a) ? 1 : 0;
    const crossB = isCrossRaceId(b) ? 1 : 0;
    return crossA - crossB || (crossA ? a.localeCompare(b) : compareRaceIds(a, b));
  });
}

/** Dense per-section sum of a race's nominal votes. */
export function nominalTotals(shard: Shard, raceId: RaceId): number[] {
  const totals = new Array<number>(shard.section.length).fill(0);
  const votes = shard.votes[raceId];
  if (!votes) return totals;
  for (const [i, position] of votes.sec.entries()) {
    const current = totals[position];
    if (current === undefined) continue; // decoder already range-checked; be safe
    totals[position] = current + (votes.n[i] ?? 0);
  }
  return totals;
}

function candidateNames(entries: readonly CandidateEntry[] | undefined): Map<number, CandidateEntry> {
  const byNumber = new Map<number, CandidateEntry>();
  for (const entry of entries ?? []) byNumber.set(entry[0], entry);
  return byNumber;
}

/**
 * The `limit` leading candidates of a race, with dense per-section columns.
 *
 * Ranked by municipal total so the table's two share columns stay stable across
 * every section — a per-section "winner" column would silently change meaning
 * from row to row.
 */
export function leadingCandidates(shard: Shard, raceId: RaceId, limit: number): CandidateView[] {
  const votes = shard.votes[raceId];
  if (!votes) return [];

  const totals = new Map<number, number>();
  for (const [i, number] of votes.cand.entries())
    totals.set(number, (totals.get(number) ?? 0) + (votes.n[i] ?? 0));

  const leading = [...totals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0] - b[0])
    .slice(0, limit);
  if (leading.length === 0) return [];

  const columns = new Map<number, number[]>(
    leading.map(([number]) => [number, new Array<number>(shard.section.length).fill(0)]),
  );
  for (const [i, number] of votes.cand.entries()) {
    const column = columns.get(number);
    if (!column) continue;
    const position = votes.sec[i];
    if (position === undefined) continue;
    const current = column[position];
    if (current === undefined) continue;
    column[position] = current + (votes.n[i] ?? 0);
  }

  const names = candidateNames(shard.cand[raceId]);
  return leading.map(([number, total]) => {
    const entry = names.get(number);
    return {
      number,
      name: entry ? entry[1] : String(number),
      party: entry ? entry[2] : null,
      total,
      column: columns.get(number) ?? [],
    };
  });
}

/** Highest score at `index` across `scoreIds`; `null` when nothing scored it. */
export function maxScoreAt(
  shard: Shard,
  index: number,
  scoreIds: readonly ScoreRaceId[],
): number | null {
  let max: number | null = null;
  for (const raceId of scoreIds) {
    const byKey = shard.scores[raceId];
    if (!byKey) continue;
    for (const column of Object.values(byKey)) {
      const value = column[index];
      if (value === null || value === undefined) continue;
      if (max === null || value > max) max = value;
    }
  }
  return max;
}

/** Build the table's rows for one race lens. */
export function buildLens(shard: Shard, raceId: RaceId, candidateCount = 2): RaceLens {
  const candidates = leadingCandidates(shard, raceId, candidateCount);
  const scoreIds = lensScoreIds(shard, raceId);
  const nominal = nominalTotals(shard, raceId);
  const stats = shard.stats[raceId];

  const rows = shard.section.map((section, index) => {
    const aptos = stats?.aptos[index] ?? 0;
    const comp = stats?.comp[index] ?? 0;
    const denominator = nominal[index] ?? 0;
    return {
      index,
      zone: shard.zone[index] ?? 0,
      section,
      aptos,
      comp,
      turnout: aptos > 0 ? comp / aptos : null,
      nominal: denominator,
      shares: candidates.map((candidate) =>
        denominator > 0 ? (candidate.column[index] ?? 0) / denominator : null,
      ),
      maxScore: maxScoreAt(shard, index, scoreIds),
    } satisfies SectionRow;
  });

  return { raceId, candidates, scoreIds, rows };
}

/**
 * How many observations a score at `raceId` rests on, in this section.
 *
 * For a real race that is the nominal-vote denominator the detectors used. A
 * cross-race id has no votes of its own, so its sample is the round's turnout —
 * the voters whose ballots the cross-race comparison is drawn from.
 */
export function sampleSizeAt(shard: Shard, raceId: ScoreRaceId, index: number): SampleSize {
  if (!isCrossRaceId(raceId))
    return { n: nominalTotals(shard, raceId)[index] ?? 0, kind: "nominal" };

  const round = raceId.endsWith("_2") ? 2 : 1;
  const race =
    shard.races.find((id) => parseRaceId(id)?.round === round) ?? shard.races[0] ?? null;
  return { n: race ? (shard.stats[race]?.comp[index] ?? 0) : 0, kind: "voters" };
}

/**
 * Every detector score recorded for one section, across every race id.
 *
 * Cross-race ids are included on purpose: `ALL_1` / `ALL_2` are where the
 * split-ticket and digit-presence detectors write, and a panel that dropped
 * them would under-report the section.
 */
export function detectorEntries(shard: Shard, index: number): DetectorEntry[] {
  const entries: DetectorEntry[] = [];
  const samples = new Map<ScoreRaceId, SampleSize>();

  for (const raceId of scoreRaceIds(shard)) {
    const byKey = shard.scores[raceId];
    if (!byKey) continue;
    for (const [scoreKey, column] of Object.entries(byKey)) {
      const score = column[index];
      if (score === null || score === undefined) continue;
      let sample = samples.get(raceId);
      if (!sample) {
        sample = sampleSizeAt(shard, raceId, index);
        samples.set(raceId, sample);
      }
      entries.push({
        raceId,
        scoreKey,
        score,
        detail: shard.score_detail[raceId]?.[String(index)]?.[scoreKey] ?? null,
        sample,
      });
    }
  }
  return entries.sort((a, b) => b.score - a.score || a.scoreKey.localeCompare(b.scoreKey));
}

function leaveOneOut(total: number, denominator: number, part: number, base: number): number | null {
  const rest = denominator - base;
  return rest > 0 ? (total - part) / rest : null;
}

/**
 * A section's turnout next to the municipality's, that section excluded.
 *
 * Leave-one-out on purpose: a section compared against an average it is itself
 * part of looks closer to normal the smaller the municipality is.
 */
export function turnoutBaseline(shard: Shard, raceId: RaceId, index: number): LocalBaseline {
  const stats = shard.stats[raceId];
  const aptos = stats?.aptos[index] ?? 0;
  const comp = stats?.comp[index] ?? 0;
  const totalAptos = (stats?.aptos ?? []).reduce((sum, value) => sum + value, 0);
  const totalComp = (stats?.comp ?? []).reduce((sum, value) => sum + value, 0);
  return {
    value: aptos > 0 ? comp / aptos : null,
    baseline: leaveOneOut(totalComp, totalAptos, comp, aptos),
    sample: { n: aptos, kind: "voters" },
  };
}

/** A candidate's share in one section next to its leave-one-out municipal share. */
export function shareBaseline(
  candidate: CandidateView,
  nominal: readonly number[],
  index: number,
): LocalBaseline {
  const denominator = nominal[index] ?? 0;
  const part = candidate.column[index] ?? 0;
  const total = nominal.reduce((sum, value) => sum + value, 0);
  return {
    value: denominator > 0 ? part / denominator : null,
    baseline: leaveOneOut(candidate.total, total, part, denominator),
    sample: { n: denominator, kind: "nominal" },
  };
}
