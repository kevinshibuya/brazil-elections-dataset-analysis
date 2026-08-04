/**
 * React Query bindings over the static export.
 *
 * Everything below the manifest is immutable and content-hashed, so the cache
 * policy is trivial: never stale, never refetched. The manifest itself is the
 * only `no-store` fetch, and it is memoised in `./manifest`.
 */

import { QueryClient, useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  fetchBytes,
  fetchJson,
  loadManifest,
  logical,
  MissingFileError,
  resolveWith,
} from "./manifest";
import {
  decodeShard,
  decodeShardDocument,
  isSplitIndex,
  mergeShards,
  requireVersion,
  SchemaVersionError,
} from "./shards";
import type { Manifest, MunicipalitiesIndex, ScoreKey, Shard, Summary } from "./types";

/** Query keys, in one place so pages and prefetchers cannot drift. */
export const dataKeys = {
  manifest: ["manifest"] as const,
  file: (logicalName: string) => ["data-file", logicalName] as const,
  shard: (code: string) => ["shard", code] as const,
};

/**
 * The app's query client.
 *
 * Retrying a `SchemaVersionError` or a `MissingFileError` is pointless — the
 * export on the server is what it is — so those fail fast and surface as a
 * "please refresh" state instead of three seconds of spinner.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Number.POSITIVE_INFINITY,
        gcTime: 30 * 60 * 1000,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        retry: (failureCount: number, error: Error) =>
          !(error instanceof SchemaVersionError) &&
          !(error instanceof MissingFileError) &&
          failureCount < 2,
      },
    },
  });
}

/**
 * Version-gate a plain JSON export and hand it to the caller as `T`.
 *
 * The `v` stamp is checked for real; the rest of the shape is trusted, because
 * the export validation gate already proved it on the Python side and the
 * pages that consume these files own their own narrowing.
 */
function decodeVersioned<T>(raw: unknown, source: string): T {
  return requireVersion(raw, source) as unknown as T;
}

async function loadJsonExport<T>(logicalName: string): Promise<T> {
  const manifest = await loadManifest();
  return decodeVersioned<T>(await fetchJson(resolveWith(manifest, logicalName)), logicalName);
}

/**
 * Fetch one municipality's sections — split or not.
 *
 * A split municipality's `shards/{code}.json.gz` holds an index instead of
 * data; this resolves it, pulls every per-zone subshard, and merges them back
 * into `(zone, section)` order. Callers cannot tell the difference, which is
 * the whole point: splitting is an export-side size decision.
 */
export async function fetchShard(code: string): Promise<Shard> {
  const manifest = await loadManifest();
  const indexName = logical.shard(code);
  const document = await decodeShardDocument(
    await fetchBytes(resolveWith(manifest, indexName)),
    indexName,
  );
  if (!isSplitIndex(document)) return document;

  const parts = await Promise.all(
    document.zones.map(async (zone) => {
      const partName = logical.subshard(code, zone);
      return decodeShard(await fetchBytes(resolveWith(manifest, partName)), partName);
    }),
  );
  return mergeShards(parts);
}

/** The content-hash manifest — every other fetch waits on this one. */
export function useManifest(): UseQueryResult<Manifest, Error> {
  return useQuery({ queryKey: dataKeys.manifest, queryFn: loadManifest });
}

/** Any version-stamped JSON export, by logical name. */
export function useJsonExport<T>(logicalName: string): UseQueryResult<T, Error> {
  return useQuery({
    queryKey: dataKeys.file(logicalName),
    queryFn: () => loadJsonExport<T>(logicalName),
  });
}

/** `summary.json` — national counters and per-detector totals. */
export function useSummary(): UseQueryResult<Summary, Error> {
  return useJsonExport<Summary>(logical.summary());
}

/** `municipalities-index.json` — the columnar municipality directory. */
export function useMunicipalitiesIndex(): UseQueryResult<MunicipalitiesIndex, Error> {
  return useJsonExport<MunicipalitiesIndex>(logical.municipalitiesIndex());
}

/** `states/{UF}.json`. */
export function useStateAggregate<T>(uf: string | undefined): UseQueryResult<T, Error> {
  const name = logical.state(uf ?? "");
  return useQuery({
    queryKey: dataKeys.file(name),
    queryFn: () => loadJsonExport<T>(name),
    enabled: Boolean(uf),
  });
}

/** `anomalies/{score_key}.json` — the ranked table behind a detector page. */
export function useAnomalies<T>(scoreKey: ScoreKey | undefined): UseQueryResult<T, Error> {
  const name = logical.anomalies(scoreKey ?? "");
  return useQuery({
    queryKey: dataKeys.file(name),
    queryFn: () => loadJsonExport<T>(name),
    enabled: Boolean(scoreKey),
  });
}

/** One municipality's shard, transparently merged when the export split it. */
export function useShard(munCode: string | undefined): UseQueryResult<Shard, Error> {
  const code = munCode ?? "";
  return useQuery({
    queryKey: dataKeys.shard(code),
    queryFn: () => fetchShard(code),
    enabled: code !== "",
  });
}

/**
 * True when a query failed because the export was written by a different
 * schema version — the one error the whole app has to shout about.
 */
export function isSchemaVersionError(error: unknown): error is SchemaVersionError {
  return error instanceof SchemaVersionError;
}
