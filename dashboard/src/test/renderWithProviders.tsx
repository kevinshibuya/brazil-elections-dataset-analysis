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
import { GEO_TOPOJSON_PATH } from "../data/geo";
import { createQueryClient } from "../data/hooks";
import { resetManifestCache } from "../data/manifest";
import type { Manifest, MunicipalitiesIndex, Summary } from "../data/types";
import { I18nProvider } from "../i18n";

/**
 * Codes the fake export answers for. All of them are served the SAME golden
 * bytes — the drill-down is generic over a shard, and cutting a second real
 * fixture would only re-test the decoder.
 */
const SHARD_CODES = ["03018", "03158"] as const;

/** One municipality of the fake export, in the shape a test wants to read it. */
export interface FixtureMunicipality {
  readonly code: string;
  readonly name: string;
  readonly uf: string;
  readonly sections: number;
  /** `null` for exterior ("ZZ") municipalities — they have no place on a map. */
  readonly lat: number | null;
  readonly lon: number | null;
  /** Headline-race shares, aligned with `headline.candidates`. */
  readonly shares: readonly number[] | null;
  /** Composite-flagged sections; `null` when the composite has not run. */
  readonly flagged: number | null;
  /**
   * Vote-weighted turnout of the headline race; `null` when there is no
   * denominator. Spread across the real 0.75–0.87 band on purpose — a fixture
   * where every municipality turned out identically would bin into one colour
   * whether or not the scale is any good.
   */
  readonly turnout: number | null;
}

/**
 * The fake export's municipalities — real RR codes, names and centroids, plus
 * one exterior entry.
 *
 * `30066 MADRI` is a real TSE municipality in the `ZZ` "state" (a consulate,
 * not a place), and it is here on purpose: it is the case the choropleth must
 * leave OFF the map, and a fixture where every municipality had geometry would
 * pass whether or not the page got that right.
 *
 * Exported because the overview's acceptance test derives its expected polygon
 * count from it — `filter(m => m.lat !== null)` — so the assertion stays true
 * when this list changes.
 */
export const fixtureIndex: readonly FixtureMunicipality[] = [
  {
    code: "03000",
    name: "CAROEBE",
    uf: "RR",
    sections: 26,
    lat: 0.9146,
    lon: -59.33809,
    shares: [0.802289, 0.197711],
    flagged: 1,
    turnout: 0.863785,
  },
  {
    code: "03018",
    name: "BOA VISTA",
    uf: "RR",
    sections: 741,
    lat: 3.11799,
    lon: -60.71791,
    shares: [0.794678, 0.205322],
    flagged: 8,
    turnout: 0.772014,
  },
  {
    code: "03034",
    name: "CARACARAÍ",
    uf: "RR",
    sections: 55,
    lat: 0.99074,
    lon: -61.47151,
    shares: [0.721701, 0.278299],
    flagged: 0,
    turnout: 0.801139,
  },
  {
    code: "03158",
    name: "SÃO LUIZ",
    uf: "RR",
    sections: 22,
    lat: 0.88438,
    lon: -60.15182,
    shares: [0.722166, 0.277834],
    flagged: 1,
    turnout: 0.845297,
  },
  {
    code: "30066",
    name: "MADRI",
    uf: "ZZ",
    sections: 3,
    lat: null,
    lon: null,
    shares: [0.31, 0.69],
    flagged: 0,
    turnout: null,
  },
];

/** The municipalities the fake topology has geometry for — by construction. */
const MAPPED = fixtureIndex.filter((entry) => entry.lat !== null);

const municipalitiesIndex: MunicipalitiesIndex = {
  v: 1,
  code: fixtureIndex.map((entry) => entry.code),
  name: fixtureIndex.map((entry) => entry.name),
  uf: fixtureIndex.map((entry) => entry.uf),
  lat: fixtureIndex.map((entry) => entry.lat),
  lon: fixtureIndex.map((entry) => entry.lon),
  sections: fixtureIndex.map((entry) => entry.sections),
  headline: {
    race_id: "PRES_2",
    candidates: [
      { number: 22, name: "JAIR MESSIAS BOLSONARO" },
      { number: 13, name: "LUIZ INÁCIO LULA DA SILVA" },
    ],
  },
  headline_shares: fixtureIndex.map((entry) => entry.shares),
  flag_density: fixtureIndex.map((entry) => entry.flagged),
  turnout: fixtureIndex.map((entry) => entry.turnout),
};

/**
 * `summary.json`, trimmed to the columns the overview reads but keeping the
 * three detector outcomes that must never look alike: something flagged,
 * scored-and-clean (`t1_arithmetic`), and never computed (`digits`).
 */
const summary: Summary = {
  v: 1,
  ufs: ["RR"],
  municipalities: 15,
  sections: 1268,
  races: {
    PRES_2: {
      race: "PRES",
      round: 2,
      candidates: 2,
      total_votes: 280646,
      legenda: 0,
      sections: 1268,
      aptos: 366641,
      comparecimento: 286269,
      abstentions: 80372,
      blank: 2173,
      null_votes: 3450,
      turnout: 0.780788,
    },
  },
  detectors: {
    composite: {
      detector: "composite",
      sections_scored: 1268,
      sections_flagged: 25,
      at: "2026-08-05T01:08:47Z",
    },
    split_ticket: {
      detector: "t2_split_ticket",
      sections_scored: 1268,
      sections_flagged: 35,
      at: "2026-08-05T01:08:46Z",
    },
    extreme_dominance: {
      detector: "t2_extreme_dominance",
      sections_scored: 1268,
      sections_flagged: 18,
      at: "2026-08-05T01:08:46Z",
    },
    turnout: {
      detector: "t2_turnout",
      sections_scored: 1268,
      sections_flagged: 6,
      at: "2026-08-05T01:08:46Z",
    },
    t1_arithmetic: {
      detector: "t1_arithmetic",
      sections_scored: 1268,
      sections_flagged: 0,
      at: "2026-08-05T01:08:45Z",
    },
    digits: {
      detector: "t2_digits",
      sections_scored: 0,
      sections_flagged: 0,
      at: "2026-08-05T01:08:47Z",
    },
  },
  annulled: { candidates: 22, votes: 27160, legenda_votes: 95, municipalities: 15 },
};

/**
 * `anomalies/extreme_dominance.json` — the national top-N of one detector.
 *
 * **Deliberately not in score order.** The exporter happens to write these
 * sorted, and a fixture that copied that would prove nothing about the page:
 * the ranking would come out right whether or not the page sorts at all. Two
 * rows share a score (9.5) so the tie-break is exercised too.
 *
 * Five rows rather than one, because a one-row table cannot be out of order.
 */
const extremeDominance = {
  v: 1,
  detector: "t2_extreme_dominance",
  key: "extreme_dominance",
  top_n: 500,
  rows_scored: 2536,
  flag_threshold: 4,
  rows: [
    {
      mun_code: "03034",
      mun_name: "CARACARAÍ",
      uf: "RR",
      zone: 3,
      section: 12,
      race: "PRES",
      round: 1,
      race_id: "PRES_1",
      score: 9.5,
      detail: { k: 141, n: 168, cand: 13, p_hat: 0.174392, baseline: "municipality" },
    },
    {
      mun_code: "03000",
      mun_name: "CAROEBE",
      uf: "RR",
      zone: 4,
      section: 63,
      race: "PRES",
      round: 2,
      race_id: "PRES_2",
      score: 16,
      detail: { k: 127, n: 149, cand: 13, p_hat: 0.178221, baseline: "municipality" },
    },
    {
      mun_code: "03158",
      mun_name: "SÃO LUIZ",
      uf: "RR",
      zone: 7,
      section: 4,
      race: "PRES",
      round: 1,
      race_id: "PRES_1",
      score: 1.9,
      detail: { k: 44, n: 210, cand: 22, p_hat: 0.121004, baseline: "municipality" },
    },
    {
      mun_code: "03018",
      mun_name: "BOA VISTA",
      uf: "RR",
      zone: 5,
      section: 278,
      race: "PRES",
      round: 1,
      race_id: "PRES_1",
      score: 12,
      detail: { k: 159, n: 241, cand: 13, p_hat: 0.190553, baseline: "municipality" },
    },
    {
      mun_code: "03018",
      mun_name: "BOA VISTA",
      uf: "RR",
      zone: 5,
      section: 301,
      race: "PRES",
      round: 2,
      race_id: "PRES_2",
      score: 9.5,
      detail: { k: 150, n: 233, cand: 13, p_hat: 0.188110, baseline: "municipality" },
    },
  ],
};

/**
 * `states/RR.json` — the distribution detectors' delivery vehicle.
 *
 * These three produce no per-section score at all; their whole output is the
 * `payloads` block below. The digit strata carry the case that matters most:
 * one stratum is `skipped` with a reason and **no `p` key at all**, next to
 * two that were computed. A page that filled that hole with a zero, or that
 * rendered the skipped cell like the others, would be reporting a test it
 * never ran — so the fixture makes sure both shapes are on screen at once.
 *
 * The fingerprint grid is 8 × 8 rather than the real 100 × 100: the heatmap is
 * generic over `bins`, and 10 000 fixture cells would test the JSON parser.
 */
const stateRR = {
  v: 1,
  uf: "RR",
  sections: 1268,
  races: {},
  detectors: {},
  payloads: {
    digits: {
      uf: "RR",
      buckets: ["large", "mid", "small"],
      n_counts: 5068,
      strata: [
        {
          uf: "RR",
          test: "second_digit",
          bucket: "large",
          n: 2962,
          skipped: false,
          chi2: 113.17133,
          dof: 9,
          p: 3.32e-20,
          digit_counts: [395, 356, 304, 225, 228, 254, 241, 288, 309, 362],
          digit_freqs: [
            0.133356, 0.120189, 0.102633, 0.075962, 0.076975, 0.085753, 0.081364, 0.097232,
            0.104321, 0.122215,
          ],
          expected_freqs: [
            0.119679, 0.11389, 0.108821, 0.10433, 0.100308, 0.096677, 0.093375, 0.090352, 0.08757,
            0.084997,
          ],
        },
        {
          uf: "RR",
          test: "last_digit",
          bucket: "large",
          n: 1451,
          skipped: false,
          chi2: 11.667126,
          dof: 9,
          p: 0.232732,
          digit_counts: [133, 139, 126, 133, 152, 153, 146, 143, 152, 174],
          digit_freqs: [
            0.091661, 0.095796, 0.086837, 0.091661, 0.104755, 0.105445, 0.10062, 0.098553, 0.104755,
            0.119917,
          ],
          expected_freqs: [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
        },
        {
          uf: "RR",
          test: "second_digit",
          bucket: "small",
          n: 41,
          skipped: true,
          reason: "fewer than 100 eligible counts",
        },
      ],
    },
    fingerprint: {
      bins: 8,
      round: 2,
      candidate: 22,
      candidate_name: "JAIR MESSIAS BOLSONARO",
      n_sections: 12,
      corner_mass: 0,
      corner_sections: 0,
      grid: [
        [0, 0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0, 0],
        [0, 0, 1, 0, 0, 0, 0, 0],
        [0, 0, 2, 3, 0, 0, 0, 0],
        [0, 0, 0, 4, 1, 0, 0, 0],
        [0, 0, 0, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0, 0],
      ],
      rounds: {
        "1": {
          round: 1,
          candidate: 22,
          candidate_name: "JAIR MESSIAS BOLSONARO",
          n_sections: 9,
          corner_mass: 0,
          corner_sections: 0,
          grid: [
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 1, 0, 0, 0, 0, 0],
            [0, 0, 3, 1, 0, 0, 0, 0],
            [0, 0, 1, 2, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
          ],
        },
        "2": {
          round: 2,
          candidate: 22,
          candidate_name: "JAIR MESSIAS BOLSONARO",
          n_sections: 12,
          corner_mass: 0,
          corner_sections: 0,
          grid: [
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0, 0],
            [0, 0, 2, 3, 0, 0, 0, 0],
            [0, 0, 0, 4, 1, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0],
          ],
        },
      },
    },
    urna_model: {
      uf: "RR",
      raw_gap: -0.172997,
      within_mun_gap: -0.109717,
      ci95: [-0.160081, -0.059354],
      se: 0.025696,
      n_municipalities: 12,
      mixed_votes: 89565,
      model_sections: { ue2020: 1031, pre2020: 237, unknown: 0 },
      shares: { ue2020: 0.787165, pre2020: 0.614168 },
      votes: {
        ue2020: { valid: 237889, subject: 187258 },
        pre2020: { valid: 42757, subject: 26260 },
      },
    },
  },
};

/**
 * A synthetic topology: one 0.1° square per mapped municipality, delta-encoded
 * and quantized exactly the way mapshaper writes the real one.
 *
 * Synthetic on purpose — the shipped 1.9 MB asset has no business in a unit
 * test — but encoded for real, so the decoder's quantization transform, its
 * arc indirection and its Polygon/MultiPolygon split are all exercised. The
 * geometries are derived from `MAPPED`, so the topology covers exactly the
 * lat-bearing municipalities and cannot drift from the index above.
 */
const topology = {
  type: "Topology",
  transform: { scale: [0.001, 0.001], translate: [-62, 0] },
  arcs: MAPPED.map((_, i) => [
    [i * 150, 0],
    [100, 0],
    [0, 100],
    [-100, 0],
    [0, -100],
  ]),
  objects: {
    municipalities: {
      type: "GeometryCollection",
      geometries: MAPPED.map((entry, i) => ({
        type: "Polygon",
        arcs: [[i]],
        properties: { code: entry.code, uf: entry.uf },
      })),
    },
    states: {
      type: "GeometryCollection",
      geometries: [
        {
          type: "MultiPolygon",
          arcs: MAPPED.map((_, i) => [[i]]),
          properties: { uf: "RR" },
        },
      ],
    },
  },
};

/** Logical name → physical (hashed) path, exactly as the exporter writes it. */
const PATHS: Record<string, string> = {
  "summary.json": "summary.d1fe50f6.json",
  "municipalities-index.json": "municipalities-index.1cd1d112.json",
  "anomalies/extreme_dominance.json": "anomalies/extreme_dominance.7c40b91a.json",
  "states/RR.json": "states/RR.3ae6d208.json",
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
  "summary.json": summary,
  "municipalities-index.json": municipalitiesIndex,
  "anomalies/extreme_dominance.json": extremeDominance,
  "states/RR.json": stateRR,
};

/**
 * Note what is NOT in `PATHS`: the other detectors' anomalies files and
 * `anomalies/clustering.json`. That is the point — a logical name the manifest
 * does not carry is how "this export never computed that" reaches the UI, and
 * the fake export has to be able to produce that state as well as the happy
 * one.
 */

function serve(url: string): Response {
  if (url.endsWith("data-manifest.json")) return new Response(JSON.stringify(manifest));
  // The topology is a bundle asset, not an export file: it is fetched by its
  // own stable path and never appears in the manifest.
  if (url.endsWith(GEO_TOPOJSON_PATH)) return new Response(JSON.stringify(topology));

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
