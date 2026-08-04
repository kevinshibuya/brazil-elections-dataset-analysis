/**
 * The derivations the table and panel are built on, against the real golden
 * shard — because the failure mode they exist to prevent is silent.
 *
 * `votes` is a sparse COO triple: a candidate with no entry in a section got
 * zero votes there. Walking it in order instead of scattering it produces
 * columns that are the right LENGTH and the wrong ALIGNMENT, which renders as
 * a plausible table of wrong numbers. So the assertions here are totals and
 * positions, not shapes.
 */

import { describe, expect, it } from "vitest";

import { goldenBytes, goldenExpected } from "../../data/__fixtures__";
import { decodeShard } from "../../data/shards";
import type { Shard } from "../../data/types";
import {
  buildLens,
  detectorEntries,
  leadingCandidates,
  nominalTotals,
  sampleSizeAt,
  scoreRaceIds,
  shareBaseline,
} from "./model";

const shard: Shard = await decodeShard(goldenBytes);
const raceTotals: Record<string, number> = goldenExpected.raceTotals;

describe("dense columns from a sparse COO", () => {
  it("sums nominal votes back to the exporter's race totals", () => {
    for (const race of shard.races)
      expect(nominalTotals(shard, race).reduce((sum, value) => sum + value, 0)).toBe(
        raceTotals[race],
      );
  });

  it("gives every candidate a full-length column that sums to its municipal total", () => {
    // DEPEST_1 has 153 candidates over 22 sections — overwhelmingly zero pairs,
    // so this is where an order-walking bug would show up first.
    for (const candidate of leadingCandidates(shard, "DEPEST_1", 5)) {
      expect(candidate.column).toHaveLength(shard.section.length);
      expect(candidate.column.reduce((sum, value) => sum + value, 0)).toBe(candidate.total);
    }
  });

  it("keeps a candidate's votes on the section the COO recorded them for", () => {
    const votes = shard.votes["PRES_1"];
    if (!votes) throw new Error("fixture lost PRES_1 votes");
    const [candidate] = leadingCandidates(shard, "PRES_1", 1);
    if (!candidate) throw new Error("fixture lost PRES_1 candidates");

    const recorded = new Map<number, number>();
    for (const [i, position] of votes.sec.entries())
      if (votes.cand[i] === candidate.number) recorded.set(position, votes.n[i] ?? 0);

    for (const [index] of candidate.column.entries())
      expect(candidate.column[index]).toBe(recorded.get(index) ?? 0);
  });
});

describe("lens rows", () => {
  it("computes shares against the section's own nominal denominator", () => {
    const lens = buildLens(shard, "PRES_1");
    const nominal = nominalTotals(shard, "PRES_1");
    const [leader] = lens.candidates;
    if (!leader) throw new Error("fixture lost PRES_1 candidates");

    for (const row of lens.rows) {
      expect(row.nominal).toBe(nominal[row.index]);
      const expected = row.nominal > 0 ? (leader.column[row.index] ?? 0) / row.nominal : null;
      expect(row.shares[0]).toBe(expected);
    }
  });

  it("carries the highest score in the lens scope onto the row", () => {
    const lens = buildLens(shard, "PRES_1");
    expect(lens.scoreIds).toEqual(["PRES_1", "ALL_1"]);

    const flagged = lens.rows.filter((row) => (row.maxScore ?? 0) >= 2);
    expect(flagged.length).toBeGreaterThan(0);
    for (const row of lens.rows) {
      const direct = Math.max(
        ...lens.scoreIds.flatMap((race) =>
          Object.values(shard.scores[race] ?? {}).map((column) => column[row.index] ?? -Infinity),
        ),
      );
      expect(row.maxScore).toBe(direct);
    }
  });
});

describe("what the panel reads", () => {
  const flaggedIndex = 13; // the fixture's one section with recorded detail

  it("lists every race id, cross-race entries included", () => {
    expect(scoreRaceIds(shard)).toEqual([
      "PRES_1",
      "PRES_2",
      "GOV_1",
      "SEN_1",
      "DEPFED_1",
      "DEPEST_1",
      "ALL_1",
      "ALL_2",
    ]);

    const entries = detectorEntries(shard, flaggedIndex);
    expect(entries.some((entry) => entry.raceId === "ALL_1")).toBe(true);
    expect(entries.some((entry) => entry.raceId === "ALL_2")).toBe(true);
  });

  it("pairs the detector's own n with the sample it was computed over", () => {
    const entry = detectorEntries(shard, flaggedIndex).find(
      (candidate) => candidate.raceId === "PRES_1" && candidate.scoreKey === "extreme_dominance",
    );
    if (!entry?.detail) throw new Error("fixture lost the flagged section's detail");

    expect(entry.sample.kind).toBe("nominal");
    expect(entry.detail["n"]).toBe(entry.sample.n);
    expect(entry.detail["baseline"]).toBe("municipality");
  });

  it("uses the round's turnout as the sample behind a cross-race score", () => {
    const sample = sampleSizeAt(shard, "ALL_2", flaggedIndex);
    expect(sample.kind).toBe("voters");
    expect(sample.n).toBe(shard.stats["PRES_2"]?.comp[flaggedIndex]);
  });

  it("excludes the section from the baseline it is compared against", () => {
    const lens = buildLens(shard, "PRES_1");
    const [leader] = lens.candidates;
    if (!leader) throw new Error("fixture lost PRES_1 candidates");
    const nominal = lens.rows.map((row) => row.nominal);
    const total = nominal.reduce((sum, value) => sum + value, 0);

    const local = shareBaseline(leader, nominal, flaggedIndex);
    const inclusive = leader.total / total;
    const rest =
      (leader.total - (leader.column[flaggedIndex] ?? 0)) /
      (total - (nominal[flaggedIndex] ?? 0));

    expect(local.baseline).toBeCloseTo(rest, 12);
    expect(local.baseline).not.toBeCloseTo(inclusive, 6);
  });
});
