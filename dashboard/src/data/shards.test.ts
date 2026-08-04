import { describe, expect, it } from "vitest";

import { goldenBytes, goldenExpected, withVersion } from "./__fixtures__";
import { decodeShard, SchemaVersionError } from "./shards";

describe("shard decoder ↔ Python encoder", () => {
  it("round-trips the golden shard with matching totals", async () => {
    const shard = await decodeShard(goldenBytes);
    expect(shard.v).toBe(1);
    const total = shard.votes["PRES_1"]!.n.reduce((a, b) => a + b, 0);
    expect(total).toBe(goldenExpected.pres1Total);
    expect(shard.zone.length).toBe(shard.section.length);
    expect(shard.races.some((r) => !r.startsWith("PRES"))).toBe(true); // golden covers a non-PRES race
    for (const k of Object.keys(shard.scores))
      expect(shard.races.includes(k) || /^ALL_[12]$/.test(k)).toBe(true);
  });

  it("keeps stats arrays dense and aligned with the section index", async () => {
    const shard = await decodeShard(goldenBytes);
    const n = shard.section.length;
    for (const race of shard.races) {
      const s = shard.stats[race]!;
      for (const col of [s.aptos, s.comp, s.blank, s.null_votes, s.legenda])
        expect(col.length).toBe(n);
    }
  });

  it("refuses schema version mismatch", async () => {
    await expect(decodeShard(await withVersion(goldenBytes, 2))).rejects.toBeInstanceOf(
      SchemaVersionError,
    );
  });
});
