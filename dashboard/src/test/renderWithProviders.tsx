/**
 * Render a page the way the app does — router, query client, locale — against
 * a fake `data/` export served from the golden fixture.
 *
 * The fixture is the REAL Python-encoded RR shard (`data/fixtures/golden_shard/
 * 03158.json.gz`, embedded as base64 by the data slice), served through a real
 * content-hashed manifest. That matters: the page's data path is
 * manifest → hashed URL → gzip bytes → decoder, and a test that handed it a
 * plain JS object would prove none of it.
 *
 * Nothing here reads `public/data/`, so the suite is hermetic and passes on a
 * clean checkout where the export has not been generated.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import { useState, type ReactElement, type ReactNode } from "react";
import { MemoryRouter } from "react-router";

import { goldenBytes } from "../data/__fixtures__";
import { createQueryClient } from "../data/hooks";
import { resetManifestCache } from "../data/manifest";
import type { Manifest, MunicipalitiesIndex } from "../data/types";
import { I18nProvider } from "../i18n";

/**
 * Codes the fake export answers for. All of them are served the SAME golden
 * bytes — the drill-down is generic over a shard, and cutting a second real
 * fixture would only re-test the decoder.
 */
const SHARD_CODES = ["03018", "03158"] as const;

const MUNICIPALITIES: readonly { code: string; name: string; sections: number }[] = [
  { code: "03000", name: "CAROEBE", sections: 26 },
  { code: "03018", name: "BOA VISTA", sections: 741 },
  { code: "03034", name: "CARACARAÍ", sections: 55 },
  { code: "03158", name: "SÃO LUIZ", sections: 22 },
];

const municipalitiesIndex: MunicipalitiesIndex = {
  v: 1,
  code: MUNICIPALITIES.map((entry) => entry.code),
  name: MUNICIPALITIES.map((entry) => entry.name),
  uf: MUNICIPALITIES.map(() => "RR"),
  lat: MUNICIPALITIES.map(() => null),
  lon: MUNICIPALITIES.map(() => null),
  sections: MUNICIPALITIES.map((entry) => entry.sections),
  headline: { race_id: "PRES_2", candidates: [] },
  headline_shares: MUNICIPALITIES.map(() => null),
  flag_density: MUNICIPALITIES.map(() => null),
};

/** Logical name → physical (hashed) path, exactly as the exporter writes it. */
const PATHS: Record<string, string> = {
  "municipalities-index.json": "municipalities-index.1cd1d112.json",
  ...Object.fromEntries(
    SHARD_CODES.map((code) => [`shards/${code}.json.gz`, `shards/${code}.f1x7u2e5.json.gz`]),
  ),
};

const manifest: Manifest = {
  v: 1,
  generated: "2026-08-04T20:56:21Z",
  files: Object.fromEntries(
    Object.entries(PATHS).map(([logical, path]) => [logical, { path, sha256: "0".repeat(64), bytes: 0 }]),
  ),
  count: Object.keys(PATHS).length,
  bytes: 0,
};

const JSON_BODIES: Record<string, unknown> = {
  "municipalities-index.json": municipalitiesIndex,
};

function serve(url: string): Response {
  if (url.endsWith("data-manifest.json")) return new Response(JSON.stringify(manifest));

  for (const [logical, path] of Object.entries(PATHS)) {
    if (!url.endsWith(path)) continue;
    const body = JSON_BODIES[logical];
    if (body !== undefined) return new Response(JSON.stringify(body));
    // Every shard is the golden one; a fresh copy per response so a consumed
    // body can never affect the next fetch.
    return new Response(new Uint8Array(goldenBytes));
  }
  return new Response("not found", { status: 404 });
}

let installed = false;

/** Point `fetch` at the fake export. Idempotent; safe to call per render. */
export function installFixtureExport(): void {
  if (!installed) {
    globalThis.fetch = ((input: RequestInfo | URL) =>
      Promise.resolve(serve(String(input)))) as typeof fetch;
    installed = true;
  }
  // The manifest is memoised for the lifetime of the module, and each test
  // gets its own query cache — so it has to be forgotten between renders.
  resetManifestCache();
}

function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <MemoryRouter>{children}</MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}

/** `render`, wrapped in the providers every page assumes, over the fixtures. */
export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult {
  installFixtureExport();
  return render(ui, { wrapper: Providers, ...options });
}
