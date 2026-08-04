/**
 * Shard decoding: raw gzip bytes → a validated, typed {@link Shard}.
 *
 * `.json.gz` exports are RAW GZIP ON DISK, and the client decompresses them
 * itself via `DecompressionStream("gzip")` rather than trusting the CDN.
 *
 * BUT it cannot assume it always gets those bytes: static servers routinely
 * infer `Content-Encoding: gzip` from the `.gz` extension, in which case the
 * browser has already inflated the body and handing it to a gunzip stream
 * fails. `vite preview` (sirv) does exactly this — measured, not hypothetical —
 * so the decoder sniffs the gzip magic number (`1f 8b`) and inflates only when
 * the bytes really are compressed. Do not "simplify" that away in either
 * direction; both servings are real.
 *
 * This module also owns the validation primitives shared with
 * {@link ./manifest} and the split-shard merge, because `src/data/` is a
 * four-module contract (`types`, `manifest`, `shards`, `hooks`).
 */

import {
  SCHEMA_VERSION,
  compareRaceIds,
  isCrossRaceId,
  parseRaceId,
  type Bytes,
  type CandidateEntry,
  type JsonValue,
  type RaceId,
  type ScoreColumn,
  type ScoreDetail,
  type ScoreKey,
  type ScoreRaceId,
  type SectionStats,
  type Shard,
  type ShardDocument,
  type SplitIndex,
  type VotesCOO,
} from "./types";

/** The export was written by a different schema version than this build reads. */
export class SchemaVersionError extends Error {
  readonly expected = SCHEMA_VERSION;
  readonly found: unknown;
  readonly source: string;

  constructor(found: unknown, source = "export") {
    super(
      `${source}: schema version ${String(found)} is not supported ` +
        `(this build reads v=${SCHEMA_VERSION})`,
    );
    this.name = "SchemaVersionError";
    this.found = found;
    this.source = source;
  }
}

/** The document carries the right version but not the right shape. */
export class ShardShapeError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "ShardShapeError";
  }
}

// ---------------------------------------------------------------------------
// validation primitives
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function requireRecord(value: unknown, where: string): Record<string, unknown> {
  if (!isRecord(value)) throw new ShardShapeError(`${where}: expected an object`);
  return value;
}

function requireString(value: unknown, where: string): string {
  if (typeof value !== "string") throw new ShardShapeError(`${where}: expected a string`);
  return value;
}

function requireNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new ShardShapeError(`${where}: expected a finite number`);
  return value;
}

function requireArray(value: unknown, where: string): unknown[] {
  if (!Array.isArray(value)) throw new ShardShapeError(`${where}: expected an array`);
  return value;
}

function requireNumberArray(value: unknown, where: string, length?: number): number[] {
  const raw = requireArray(value, where);
  if (length !== undefined && raw.length !== length)
    throw new ShardShapeError(`${where}: expected ${length} entries, got ${raw.length}`);
  return raw.map((entry, i) => requireNumber(entry, `${where}[${i}]`));
}

function requireStringArray(value: unknown, where: string): string[] {
  return requireArray(value, where).map((entry, i) => requireString(entry, `${where}[${i}]`));
}

/**
 * Reads and enforces the `v` stamp. It is the single version gate for every
 * exported file — manifest and data alike.
 *
 * @throws {SchemaVersionError} when `v` is present but not {@link SCHEMA_VERSION}.
 */
export function requireVersion(document: unknown, source: string): Record<string, unknown> {
  const record = requireRecord(document, source);
  const version = record["v"];
  if (typeof version !== "number")
    throw new ShardShapeError(`${source}: missing numeric "v" schema stamp`);
  if (version !== SCHEMA_VERSION) throw new SchemaVersionError(version, source);
  return record;
}

// ---------------------------------------------------------------------------
// gunzip
// ---------------------------------------------------------------------------

function asBytes(input: Bytes): Uint8Array<ArrayBuffer> {
  if (!ArrayBuffer.isView(input)) return new Uint8Array(input);
  const copy = new Uint8Array(input.byteLength);
  copy.set(new Uint8Array(input.buffer, input.byteOffset, input.byteLength));
  return copy;
}

/**
 * Decompress raw gzip bytes to text.
 *
 * Fed through a one-chunk `ReadableStream` rather than `Blob.stream()`: the
 * two are equivalent in a browser, but jsdom's `Blob` has no `stream()`, and
 * the decoder has to be testable.
 */
export async function gunzipText(bytes: Bytes): Promise<string> {
  const view = asBytes(bytes);
  // Typed as `BufferSource` so it lines up with `DecompressionStream`'s writable side.
  const source = new ReadableStream<BufferSource>({
    start(controller) {
      controller.enqueue(view);
      controller.close();
    },
  });
  return new Response(source.pipeThrough(new DecompressionStream("gzip"))).text();
}

/** True when `bytes` starts with the gzip magic number. */
export function isGzipped(bytes: Bytes): boolean {
  const view = asBytes(bytes);
  return view[0] === 0x1f && view[1] === 0x8b;
}

/**
 * Parse a `.json.gz` body, inflating it first if it actually arrived
 * compressed. See the module docstring: whether it did depends on the server,
 * not on the file.
 */
export async function parseExportBytes(bytes: Bytes, source = "shard"): Promise<unknown> {
  let text: string;
  if (isGzipped(bytes)) {
    try {
      text = await gunzipText(bytes);
    } catch (cause) {
      throw new ShardShapeError(`${source}: gzip decompression failed`, { cause });
    }
  } else {
    text = new TextDecoder().decode(asBytes(bytes));
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ShardShapeError(`${source}: payload is not valid JSON`);
  }
}

// ---------------------------------------------------------------------------
// decoding
// ---------------------------------------------------------------------------

function decodeCandidates(
  value: unknown,
  races: readonly RaceId[],
  where: string,
): Record<RaceId, readonly CandidateEntry[]> {
  const record = requireRecord(value, where);
  const out: Record<RaceId, readonly CandidateEntry[]> = {};
  for (const [race, raw] of Object.entries(record)) {
    if (!races.includes(race))
      throw new ShardShapeError(`${where}: race ${race} is not in "races"`);
    out[race] = requireArray(raw, `${where}.${race}`).map((entry, i) => {
      const tuple = requireArray(entry, `${where}.${race}[${i}]`);
      if (tuple.length !== 3)
        throw new ShardShapeError(`${where}.${race}[${i}]: expected [number, name, party]`);
      const party = tuple[2];
      if (party !== null && typeof party !== "number")
        throw new ShardShapeError(`${where}.${race}[${i}][2]: expected a number or null`);
      return [
        requireNumber(tuple[0], `${where}.${race}[${i}][0]`),
        requireString(tuple[1], `${where}.${race}[${i}][1]`),
        party,
      ] as const satisfies CandidateEntry;
    });
  }
  return out;
}

const STAT_FIELDS = ["aptos", "comp", "blank", "null_votes", "legenda"] as const;

function decodeStats(
  value: unknown,
  races: readonly RaceId[],
  sections: number,
  where: string,
): Record<RaceId, SectionStats> {
  const record = requireRecord(value, where);
  const out: Record<RaceId, SectionStats> = {};
  for (const race of races) {
    const raw = requireRecord(record[race], `${where}.${race}`);
    const columns = STAT_FIELDS.map((field) =>
      requireNumberArray(raw[field], `${where}.${race}.${field}`, sections),
    );
    const [aptos, comp, blank, nullVotes, legenda] = columns;
    // Unreachable: STAT_FIELDS has five entries. Here to satisfy the checker.
    if (!aptos || !comp || !blank || !nullVotes || !legenda)
      throw new ShardShapeError(`${where}.${race}: missing stat columns`);
    out[race] = { aptos, comp, blank, null_votes: nullVotes, legenda };
  }
  return out;
}

function decodeVotes(
  value: unknown,
  races: readonly RaceId[],
  sections: number,
  where: string,
): Record<RaceId, VotesCOO> {
  const record = requireRecord(value, where);
  const out: Record<RaceId, VotesCOO> = {};
  for (const race of races) {
    const raw = requireRecord(record[race], `${where}.${race}`);
    const sec = requireNumberArray(raw["sec"], `${where}.${race}.sec`);
    const cand = requireNumberArray(raw["cand"], `${where}.${race}.cand`, sec.length);
    const n = requireNumberArray(raw["n"], `${where}.${race}.n`, sec.length);
    for (const [i, position] of sec.entries())
      if (position < 0 || position >= sections)
        throw new ShardShapeError(
          `${where}.${race}.sec[${i}]: section index ${position} out of range 0..${sections - 1}`,
        );
    out[race] = { sec, cand, n };
  }
  return out;
}

function requireScoreRaceId(id: string, races: readonly RaceId[], where: string): ScoreRaceId {
  if (races.includes(id) || isCrossRaceId(id)) return id;
  throw new ShardShapeError(
    `${where}: score race id ${id} is neither one of this shard's races nor ALL_1/ALL_2`,
  );
}

function decodeScores(
  value: unknown,
  races: readonly RaceId[],
  sections: number,
  where: string,
): Record<ScoreRaceId, Record<ScoreKey, ScoreColumn>> {
  const record = requireRecord(value, where);
  const out: Record<ScoreRaceId, Record<ScoreKey, ScoreColumn>> = {};
  for (const [race, raw] of Object.entries(record)) {
    requireScoreRaceId(race, races, where);
    const byKey = requireRecord(raw, `${where}.${race}`);
    const columns: Record<ScoreKey, ScoreColumn> = {};
    for (const [key, column] of Object.entries(byKey)) {
      const at = `${where}.${race}.${key}`;
      const entries = requireArray(column, at);
      if (entries.length !== sections)
        throw new ShardShapeError(`${at}: expected ${sections} entries, got ${entries.length}`);
      columns[key] = entries.map((entry, i) =>
        entry === null ? null : requireNumber(entry, `${at}[${i}]`),
      );
    }
    out[race] = columns;
  }
  return out;
}

function decodeScoreDetail(
  value: unknown,
  races: readonly RaceId[],
  sections: number,
  where: string,
): Record<ScoreRaceId, Record<string, Record<ScoreKey, ScoreDetail>>> {
  const record = requireRecord(value, where);
  const out: Record<ScoreRaceId, Record<string, Record<ScoreKey, ScoreDetail>>> = {};
  for (const [race, raw] of Object.entries(record)) {
    requireScoreRaceId(race, races, where);
    const bySection = requireRecord(raw, `${where}.${race}`);
    const details: Record<string, Record<ScoreKey, ScoreDetail>> = {};
    for (const [sectionIndex, payload] of Object.entries(bySection)) {
      const at = `${where}.${race}["${sectionIndex}"]`;
      const index = Number(sectionIndex);
      if (!Number.isInteger(index) || index < 0 || index >= sections)
        throw new ShardShapeError(`${at}: not a section index in 0..${sections - 1}`);
      const byKey = requireRecord(payload, at);
      const keyed: Record<ScoreKey, ScoreDetail> = {};
      for (const [key, detail] of Object.entries(byKey))
        keyed[key] = requireRecord(detail, `${at}.${key}`) as ScoreDetail;
      details[sectionIndex] = keyed;
    }
    out[race] = details;
  }
  return out;
}

function decodeSplitIndex(record: Record<string, unknown>, source: string): SplitIndex {
  return {
    v: SCHEMA_VERSION,
    split: true,
    code: requireString(record["code"], `${source}.code`),
    name: requireString(record["name"], `${source}.name`),
    uf: requireString(record["uf"], `${source}.uf`),
    zones: requireNumberArray(record["zones"], `${source}.zones`),
  };
}

function decodeFullShard(record: Record<string, unknown>, source: string): Shard {
  const races = requireStringArray(record["races"], `${source}.races`);
  for (const race of races)
    if (!parseRaceId(race))
      throw new ShardShapeError(
        `${source}.races: ${race} is not a "<RACE>_<round>" id — race ids always carry the round`,
      );

  const zone = requireNumberArray(record["zone"], `${source}.zone`);
  const section = requireNumberArray(record["section"], `${source}.section`, zone.length);
  const sections = zone.length;

  const shard: Shard = {
    v: SCHEMA_VERSION,
    code: requireString(record["code"], `${source}.code`),
    name: requireString(record["name"], `${source}.name`),
    uf: requireString(record["uf"], `${source}.uf`),
    races,
    cand: decodeCandidates(record["cand"], races, `${source}.cand`),
    zone,
    section,
    stats: decodeStats(record["stats"], races, sections, `${source}.stats`),
    votes: decodeVotes(record["votes"], races, sections, `${source}.votes`),
    scores: decodeScores(record["scores"], races, sections, `${source}.scores`),
    score_detail: decodeScoreDetail(
      record["score_detail"],
      races,
      sections,
      `${source}.score_detail`,
    ),
  };

  const zoneOnly = record["zone_only"];
  if (zoneOnly === undefined) return shard;
  return { ...shard, zone_only: requireNumber(zoneOnly, `${source}.zone_only`) };
}

/** True when `shards/{code}.json.gz` held a split index instead of real data. */
export function isSplitIndex(document: ShardDocument): document is SplitIndex {
  return "split" in document && document.split === true;
}

/**
 * Decode `shards/{code}.json.gz`, which is either a full shard or — for an
 * oversized municipality — the split index pointing at its per-zone subshards.
 *
 * @throws {SchemaVersionError} when `v !== 1`.
 * @throws {ShardShapeError} when the payload does not match the frozen schema.
 */
export async function decodeShardDocument(
  bytes: Bytes,
  source = "shard",
): Promise<ShardDocument> {
  const record = requireVersion(await parseExportBytes(bytes, source), source);
  return record["split"] === true
    ? decodeSplitIndex(record, source)
    : decodeFullShard(record, source);
}

/**
 * Decode a full shard. Use {@link decodeShardDocument} (or the `useShard`
 * hook) when the file may be a split index.
 *
 * @throws {SchemaVersionError} when `v !== 1`.
 */
export async function decodeShard(bytes: Bytes, source = "shard"): Promise<Shard> {
  const document = await decodeShardDocument(bytes, source);
  if (isSplitIndex(document))
    throw new ShardShapeError(`${source}: expected a full shard, got a split index`);
  return document;
}

// ---------------------------------------------------------------------------
// merging split shards
// ---------------------------------------------------------------------------

interface Slot {
  readonly zone: number;
  readonly section: number;
  readonly part: number;
  readonly index: number;
}

/**
 * Merge the per-zone subshards of a split municipality back into the single
 * shard the rest of the app expects — sections re-sorted into global
 * `(zone, section)` order, every dense column realigned, the sparse vote COO
 * re-emitted in `(sec, cand)` order, and `score_detail` section keys remapped.
 *
 * Splitting is an export-side size optimisation; nothing above `useShard`
 * should be able to tell that it happened.
 */
export function mergeShards(parts: readonly Shard[]): Shard {
  const first = parts[0];
  if (!first) throw new ShardShapeError("mergeShards: no subshards to merge");
  if (parts.length === 1) {
    if (first.zone_only === undefined) return first;
    const { zone_only: _dropped, ...rest } = first;
    return rest;
  }

  const slots: Slot[] = [];
  for (const [part, shard] of parts.entries()) {
    if (shard.code !== first.code)
      throw new ShardShapeError(
        `mergeShards: subshards belong to different municipalities (${first.code} vs ${shard.code})`,
      );
    for (const [index, zone] of shard.zone.entries())
      slots.push({ zone, section: shard.section[index] ?? 0, part, index });
  }
  slots.sort((a, b) => a.zone - b.zone || a.section - b.section || a.part - b.part);
  for (const [i, slot] of slots.entries()) {
    const previous = slots[i - 1];
    if (previous && previous.zone === slot.zone && previous.section === slot.section)
      throw new ShardShapeError(
        `mergeShards: section (zone=${slot.zone}, section=${slot.section}) appears in two subshards`,
      );
  }
  const total = slots.length;

  /** merged position → source slot, per part: `positions[part][sourceIndex]`. */
  const positions = parts.map((shard) => new Array<number>(shard.zone.length).fill(-1));
  for (const [merged, slot] of slots.entries()) {
    const map = positions[slot.part];
    if (map) map[slot.index] = merged;
  }

  const races = [...new Set(parts.flatMap((shard) => [...shard.races]))].sort(compareRaceIds);

  const cand: Record<RaceId, readonly CandidateEntry[]> = {};
  for (const race of races) {
    const byNumber = new Map<number, CandidateEntry>();
    for (const shard of parts)
      for (const entry of shard.cand[race] ?? [])
        if (!byNumber.has(entry[0])) byNumber.set(entry[0], entry);
    if (byNumber.size > 0)
      cand[race] = [...byNumber.values()].sort((a, b) => a[0] - b[0]);
  }

  const stats: Record<RaceId, SectionStats> = {};
  for (const race of races) {
    const columns = STAT_FIELDS.map(() => new Array<number>(total).fill(0));
    for (const [part, shard] of parts.entries()) {
      const source = shard.stats[race];
      const map = positions[part];
      if (!source || !map) continue;
      for (const [field, name] of STAT_FIELDS.entries()) {
        const from = source[name];
        const into = columns[field];
        if (!into) continue;
        for (const [index, value] of from.entries()) {
          const merged = map[index];
          if (merged !== undefined && merged >= 0) into[merged] = value;
        }
      }
    }
    const [aptos, comp, blank, nullVotes, legenda] = columns;
    if (!aptos || !comp || !blank || !nullVotes || !legenda)
      throw new ShardShapeError("mergeShards: missing stat columns");
    stats[race] = { aptos, comp, blank, null_votes: nullVotes, legenda };
  }

  const votes: Record<RaceId, VotesCOO> = {};
  for (const race of races) {
    // merged position → the (cand, n) pairs recorded there, source order kept.
    const byPosition = new Map<number, [number, number][]>();
    for (const [part, shard] of parts.entries()) {
      const source = shard.votes[race];
      const map = positions[part];
      if (!source || !map) continue;
      for (const [i, sourceSection] of source.sec.entries()) {
        const merged = map[sourceSection];
        if (merged === undefined || merged < 0) continue;
        const bucket = byPosition.get(merged) ?? [];
        bucket.push([source.cand[i] ?? 0, source.n[i] ?? 0]);
        byPosition.set(merged, bucket);
      }
    }
    const sec: number[] = [];
    const candidates: number[] = [];
    const counts: number[] = [];
    for (let position = 0; position < total; position += 1) {
      const bucket = byPosition.get(position);
      if (!bucket) continue;
      bucket.sort((a, b) => a[0] - b[0]);
      for (const [number, count] of bucket) {
        sec.push(position);
        candidates.push(number);
        counts.push(count);
      }
    }
    votes[race] = { sec, cand: candidates, n: counts };
  }

  const scores: Record<ScoreRaceId, Record<ScoreKey, ScoreColumn>> = {};
  const scoreDetail: Record<ScoreRaceId, Record<string, Record<ScoreKey, ScoreDetail>>> = {};
  for (const [part, shard] of parts.entries()) {
    const map = positions[part];
    if (!map) continue;
    for (const [race, byKey] of Object.entries(shard.scores)) {
      const target = (scores[race] ??= {});
      for (const [key, column] of Object.entries(byKey)) {
        const into = (target[key] as (number | null)[] | undefined) ?? new Array<number | null>(total).fill(null);
        target[key] = into;
        for (const [index, value] of column.entries()) {
          const merged = map[index];
          if (merged !== undefined && merged >= 0) into[merged] = value;
        }
      }
    }
    for (const [race, bySection] of Object.entries(shard.score_detail)) {
      const target = (scoreDetail[race] ??= {});
      for (const [sectionIndex, payload] of Object.entries(bySection)) {
        const merged = map[Number(sectionIndex)];
        if (merged !== undefined && merged >= 0) target[String(merged)] = payload;
      }
    }
  }

  return {
    v: SCHEMA_VERSION,
    code: first.code,
    name: first.name,
    uf: first.uf,
    races,
    cand,
    zone: slots.map((slot) => slot.zone),
    section: slots.map((slot) => slot.section),
    stats,
    votes,
    scores,
    score_detail: scoreDetail,
  };
}

/** Re-export so callers can keep detector payloads typed without a second import. */
export type { Bytes, JsonValue };
