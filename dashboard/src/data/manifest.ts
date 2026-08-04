/**
 * The manifest is the only way to turn a logical file name into a URL.
 *
 * Every exported file is immutable and content-hashed
 * (`summary.json` → `summary.1967edd0.json`), so a physical URL can be cached
 * forever — and a logical name can NEVER be string-templated into a URL, it
 * would 404. `data/data-manifest.json` is the single mutable file: fetched
 * with `cache: "no-store"`, it maps logical → physical for the current export.
 */

import { requireVersion, ShardShapeError } from "./shards";
import type { Manifest, ManifestEntry, ScoreKey } from "./types";

/** Path of the manifest itself, relative to the site root. */
export const MANIFEST_LOGICAL_PATH = "data/data-manifest.json";

/** Absolute URL of a file under `data/`, honouring the Vite base path. */
export function dataUrl(relativePath: string): string {
  const base = import.meta.env.BASE_URL;
  return `${base.endsWith("/") ? base : `${base}/`}data/${relativePath}`;
}

/** Logical names, spelled once so callers never hand-build one. */
export const logical = {
  summary: (): string => "summary.json",
  municipalitiesIndex: (): string => "municipalities-index.json",
  annulledCandidacies: (): string => "annulled-candidacies.json",
  state: (uf: string): string => `states/${uf}.json`,
  anomalies: (scoreKey: ScoreKey): string => `anomalies/${scoreKey}.json`,
  shard: (code: string): string => `shards/${code}.json.gz`,
  subshard: (code: string, zone: number): string => `shards/${code}-z${zone}.json.gz`,
};

/** A logical name the current export does not contain. */
export class MissingFileError extends Error {
  readonly logicalName: string;

  constructor(logicalName: string) {
    super(`${logicalName} is not in this export's manifest`);
    this.name = "MissingFileError";
    this.logicalName = logicalName;
  }
}

function decodeManifest(document: unknown): Manifest {
  const record = requireVersion(document, MANIFEST_LOGICAL_PATH);
  const rawFiles = record["files"];
  if (typeof rawFiles !== "object" || rawFiles === null || Array.isArray(rawFiles))
    throw new ShardShapeError(`${MANIFEST_LOGICAL_PATH}.files: expected an object`);

  const files: Record<string, ManifestEntry> = {};
  for (const [name, raw] of Object.entries(rawFiles as Record<string, unknown>)) {
    const at = `${MANIFEST_LOGICAL_PATH}.files["${name}"]`;
    if (typeof raw !== "object" || raw === null) throw new ShardShapeError(`${at}: expected an object`);
    const entry = raw as Record<string, unknown>;
    const { path, sha256, bytes } = entry;
    if (typeof path !== "string" || typeof sha256 !== "string" || typeof bytes !== "number")
      throw new ShardShapeError(`${at}: expected {path: string, sha256: string, bytes: number}`);
    files[name] = { path, sha256, bytes };
  }

  const generated = record["generated"];
  const count = record["count"];
  const bytes = record["bytes"];
  return {
    v: 1,
    generated: typeof generated === "string" ? generated : "",
    files,
    count: typeof count === "number" ? count : Object.keys(files).length,
    bytes: typeof bytes === "number" ? bytes : 0,
  };
}

let cached: Promise<Manifest> | null = null;
let loaded: Manifest | null = null;

/**
 * Fetch and validate `data/data-manifest.json`.
 *
 * Memoised for the lifetime of the page: every other file is immutable, so
 * one manifest read is the whole cache-invalidation story.
 *
 * Throws `SchemaVersionError` when the export's `v` is not this build's.
 */
export function loadManifest(): Promise<Manifest> {
  cached ??= (async () => {
    const response = await fetch(dataUrl("data-manifest.json"), { cache: "no-store" });
    if (!response.ok)
      throw new Error(`${MANIFEST_LOGICAL_PATH}: HTTP ${response.status} ${response.statusText}`);
    const manifest = decodeManifest(await response.json());
    loaded = manifest;
    return manifest;
  })().catch((error: unknown) => {
    cached = null; // a failed load must not poison the page forever
    throw error;
  });
  return cached;
}

/** Test seam: forget the memoised manifest. */
export function resetManifestCache(): void {
  cached = null;
  loaded = null;
}

/** Look a logical name up in an already-loaded manifest. */
export function entryIn(manifest: Manifest, logicalName: string): ManifestEntry | null {
  return manifest.files[logicalName] ?? null;
}

/**
 * Logical name → fetchable URL, against an explicit manifest.
 *
 * @throws {MissingFileError} when the export does not contain that file.
 */
export function resolveWith(manifest: Manifest, logicalName: string): string {
  const entry = entryIn(manifest, logicalName);
  if (!entry) throw new MissingFileError(logicalName);
  return dataUrl(entry.path);
}

/** Like {@link resolveWith}, but `null` instead of throwing when absent. */
export function tryResolveWith(manifest: Manifest, logicalName: string): string | null {
  const entry = entryIn(manifest, logicalName);
  return entry ? dataUrl(entry.path) : null;
}

/**
 * Logical name → URL, using the manifest {@link loadManifest} already fetched.
 * Await `loadManifest()` first; the data hooks always do.
 */
export function resolve(logicalName: string): string {
  if (!loaded)
    throw new Error(`resolve(${logicalName}): the manifest is not loaded yet — await loadManifest()`);
  return resolveWith(loaded, logicalName);
}

/** Fetch and parse an immutable JSON export (not gzipped). */
export async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status} ${response.statusText}`);
  return response.json();
}

/** Fetch the raw bytes of an immutable gzipped export. */
export async function fetchBytes(url: string): Promise<ArrayBuffer> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status} ${response.statusText}`);
  return response.arrayBuffer();
}

export type { Manifest, ManifestEntry };
