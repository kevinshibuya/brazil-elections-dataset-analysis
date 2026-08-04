/**
 * The two ways loading a shard can differ from "fetch one file and gunzip it":
 * the municipality was split across per-zone subshards, and the server already
 * decompressed the body for us.
 *
 * Neither is exercised by the RR export — no municipality there is over the
 * shard budget — so both are reconstructed here FROM the real golden shard
 * rather than from a hand-written toy: cut it up the way the Python encoder
 * would, and require what comes back to be the original again.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { goldenBytes } from "./__fixtures__";
import { fetchShard } from "./hooks";
import { resetManifestCache } from "./manifest";
import { decodeShard, decodeShardDocument, mergeShards } from "./shards";
import type {
  Manifest,
  ScoreColumn,
  ScoreDetail,
  SectionStats,
  Shard,
  VotesCOO,
} from "./types";

/** Carve a subshard out of `shard`, keeping only `positions` (in order). */
function subshard(shard: Shard, positions: readonly number[], zone?: number): Shard {
  const rank = new Map(positions.map((position, index) => [position, index]));
  const pick = <T,>(column: readonly T[]): T[] => positions.map((p) => column[p] as T);

  const stats: Record<string, SectionStats> = {};
  for (const race of shard.races) {
    const source = shard.stats[race];
    if (!source) continue;
    stats[race] = {
      aptos: pick(source.aptos),
      comp: pick(source.comp),
      blank: pick(source.blank),
      null_votes: pick(source.null_votes),
      legenda: pick(source.legenda),
    };
  }

  const votes: Record<string, VotesCOO> = {};
  for (const race of shard.races) {
    const source = shard.votes[race];
    if (!source) continue;
    const sec: number[] = [];
    const cand: number[] = [];
    const n: number[] = [];
    for (const [i, position] of source.sec.entries()) {
      const mapped = rank.get(position);
      if (mapped === undefined) continue;
      sec.push(mapped);
      cand.push(source.cand[i] as number);
      n.push(source.n[i] as number);
    }
    votes[race] = { sec, cand, n };
  }

  const scores: Record<string, Record<string, ScoreColumn>> = {};
  for (const [race, byKey] of Object.entries(shard.scores)) {
    const columns: Record<string, (number | null)[]> = {};
    for (const [key, column] of Object.entries(byKey)) columns[key] = pick(column);
    scores[race] = columns;
  }

  const score_detail: Record<string, Record<string, Record<string, ScoreDetail>>> = {};
  for (const [race, bySection] of Object.entries(shard.score_detail)) {
    const remapped: Record<string, Record<string, ScoreDetail>> = {};
    for (const [sectionIndex, payload] of Object.entries(bySection)) {
      const mapped = rank.get(Number(sectionIndex));
      if (mapped !== undefined) remapped[String(mapped)] = payload;
    }
    score_detail[race] = remapped;
  }

  const part: Shard = {
    v: shard.v,
    code: shard.code,
    name: shard.name,
    uf: shard.uf,
    races: shard.races,
    cand: shard.cand,
    zone: positions.map((p) => zone ?? (shard.zone[p] as number)),
    section: pick(shard.section),
    stats,
    votes,
    scores,
    score_detail,
  };
  return zone === undefined ? part : { ...part, zone_only: zone };
}

async function gzip(document: unknown): Promise<Uint8Array<ArrayBuffer>> {
  const bytes = new TextEncoder().encode(JSON.stringify(document));
  const stream = new ReadableStream<BufferSource>({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  }).pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

const evens = (n: number) => [...Array(n).keys()].filter((i) => i % 2 === 0);
const odds = (n: number) => [...Array(n).keys()].filter((i) => i % 2 === 1);

afterEach(() => {
  vi.unstubAllGlobals();
  resetManifestCache();
});

describe("transport", () => {
  it("decodes a body the server already decompressed", async () => {
    // `Content-Encoding: gzip` inferred from the `.gz` extension is a real
    // serving strategy (vite preview does it), so the same bytes have to decode
    // whether or not the browser inflated them first.
    const fromGzip = await decodeShard(goldenBytes);
    const plain = new TextEncoder().encode(JSON.stringify(fromGzip));

    expect(await decodeShardDocument(plain)).toEqual(fromGzip);
  });
});

describe("split municipalities", () => {
  it("merges interleaved subshards back into the original shard", async () => {
    const golden = await decodeShard(goldenBytes);
    const total = golden.section.length;

    // Interleaved on purpose: merging has to re-sort by (zone, section) and
    // realign every dense column, not just concatenate.
    const merged = mergeShards([
      subshard(golden, evens(total)),
      subshard(golden, odds(total)),
    ]);

    expect(merged).toEqual(golden);
  });

  it("hides splitting from the caller of fetchShard", async () => {
    const golden = await decodeShard(goldenBytes);
    const total = golden.section.length;
    const half = Math.floor(total / 2);
    const lower = [...Array(half).keys()];
    const upper = [...Array(total).keys()].slice(half);

    const code = golden.code;
    const files: Record<string, unknown> = {
      [`shards/${code}.json.gz`]: {
        v: 1,
        split: true,
        code,
        name: golden.name,
        uf: golden.uf,
        zones: [4, 9],
      },
      [`shards/${code}-z4.json.gz`]: subshard(golden, lower, 4),
      [`shards/${code}-z9.json.gz`]: subshard(golden, upper, 9),
    };

    const manifest: Manifest = {
      v: 1,
      generated: "2026-08-04T20:56:21Z",
      files: Object.fromEntries(
        Object.keys(files).map((name) => [
          name,
          { path: name.replace(".json.gz", ".deadbeef.json.gz"), sha256: "x", bytes: 0 },
        ]),
      ),
      count: Object.keys(files).length,
      bytes: 0,
    };

    const fetched: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        fetched.push(url);
        if (url.endsWith("data-manifest.json")) return new Response(JSON.stringify(manifest));
        const logical = Object.keys(files).find((name) =>
          url.endsWith(name.replace(".json.gz", ".deadbeef.json.gz")),
        );
        if (!logical) return new Response("not found", { status: 404 });
        return new Response(await gzip(files[logical]));
      }),
    );

    const shard = await fetchShard(code);

    // Every subshard was fetched through its hashed path, never a templated one.
    expect(fetched.some((url) => url.includes(`${code}-z4.deadbeef`))).toBe(true);
    expect(fetched.some((url) => url.includes(`${code}-z9.deadbeef`))).toBe(true);

    // …and what came back is the same shard a non-split export would produce,
    // modulo the zones the split itself introduced.
    expect("zone_only" in shard).toBe(false);
    expect("split" in shard).toBe(false);
    expect(shard.zone).toEqual([...lower.map(() => 4), ...upper.map(() => 9)]);
    expect({ ...shard, zone: golden.zone }).toEqual(golden);
  });
});
