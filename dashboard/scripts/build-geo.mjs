#!/usr/bin/env node
/**
 * Rebuild `public/geo/br-municipalities.topo.json` from IBGE's municipal mesh.
 *
 * The asset is COMMITTED — this script exists so it is reproducible, not so it
 * runs in CI. It needs network access (IBGE) and downloads mapshaper via npx.
 *
 *     node scripts/build-geo.mjs
 *
 * Three things are load-bearing and are the reason this is a script rather
 * than a shell one-liner in a README:
 *
 * 1. IBGE keys the mesh by IBGE code; every consumer in this project keys by
 *    TSE code. The re-key goes through `data/fixtures/mun_crosswalk_tse_ibge.csv`
 *    — the same file the exporter's centroid join uses — and THROWS on an
 *    unmapped feature rather than silently dropping a municipality off the map.
 * 2. Simplification happens BEFORE the state dissolve, so the state outlines
 *    are made of the very same arcs as the municipality borders and the two
 *    layers cannot drift apart by a pixel.
 * 3. The tolerance and quantization are constants here, printed on every run
 *    and recorded in `public/geo/README.md`, because "simplified with mapshaper"
 *    without a number is not reproducible.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const dashboardDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoDir = path.resolve(dashboardDir, "..");
const CROSSWALK = path.join(repoDir, "data/fixtures/mun_crosswalk_tse_ibge.csv");
const OUT = path.join(dashboardDir, "public/geo/br-municipalities.topo.json");

/** IBGE malhas territoriais, API v3 — the same source as `mun_centroids.csv`. */
const SOURCE_URL =
  "https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR" +
  "?formato=application/vnd.geo+json&qualidade=intermediaria&intrarregiao=municipio";

/** Pinned so a rerun cannot silently change the geometry. */
const MAPSHAPER = "mapshaper@0.6.111";

/**
 * Visvalingam (weighted) point retention. 45 % keeps ~150 k of the source's
 * 546 k points — about 27 per municipality, which still reads as a shape at
 * the size a single municipality occupies in a national view — for 474 KB
 * gzipped, a third of the 1.5 MB budget.
 */
const SIMPLIFY_PERCENTAGE = "45%";

/**
 * Coordinate quantization. Brazil's bounding box is ~44° wide, so 2e4 steps
 * are ~0.0022° ≈ 240 m: several times finer than one device pixel at any
 * zoom the page offers, and it is what makes the delta-encoded arcs compress.
 */
const QUANTIZATION = "2e4";

function readCrosswalk() {
  const lines = fs.readFileSync(CROSSWALK, "utf8").split("\n");
  const headerIndex = lines.findIndex((line) => line.startsWith("tse_code,"));
  if (headerIndex === -1) throw new Error(`${CROSSWALK}: no header row`);
  const columns = lines[headerIndex].split(",");
  const tse = columns.indexOf("tse_code");
  const uf = columns.indexOf("uf");
  const ibge = columns.indexOf("ibge_code");
  const byIbge = new Map();
  for (const line of lines.slice(headerIndex + 1)) {
    if (line.trim() === "" || line.startsWith("#")) continue;
    const parts = line.split(",");
    byIbge.set(parts[ibge], { code: parts[tse], uf: parts[uf] });
  }
  return byIbge;
}

async function main() {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "br-geo-"));
  const rawPath = path.join(work, "ibge.geojson");
  const rekeyedPath = path.join(work, "tse.geojson");

  console.log(`[geo] downloading ${SOURCE_URL}`);
  const response = await fetch(SOURCE_URL);
  if (!response.ok) throw new Error(`IBGE malhas: HTTP ${response.status}`);
  fs.writeFileSync(rawPath, Buffer.from(await response.arrayBuffer()));

  const byIbge = readCrosswalk();
  const source = JSON.parse(fs.readFileSync(rawPath, "utf8"));
  const features = source.features.map((feature) => {
    const ibgeCode = String(feature.properties.codarea);
    const mapped = byIbge.get(ibgeCode);
    // A missing code would put a hole in the map that nothing downstream can
    // detect, so it is fatal here.
    if (!mapped) throw new Error(`IBGE ${ibgeCode} has no TSE code in the crosswalk`);
    return {
      type: "Feature",
      properties: { code: mapped.code, uf: mapped.uf },
      geometry: feature.geometry,
    };
  });
  console.log(`[geo] re-keyed ${features.length} features to TSE codes`);
  fs.writeFileSync(rekeyedPath, JSON.stringify({ type: "FeatureCollection", features }));

  const args = [
    "-y",
    MAPSHAPER,
    rekeyedPath,
    "-simplify",
    "visvalingam",
    "weighted",
    `percentage=${SIMPLIFY_PERCENTAGE}`,
    "keep-shapes",
    "-filter-fields",
    "code,uf",
    "-rename-layers",
    "municipalities",
    "-dissolve2",
    "fields=uf",
    "copy-fields=",
    "name=states",
    "+",
    "-o",
    "format=topojson",
    `quantization=${QUANTIZATION}`,
    "target=municipalities,states",
    OUT,
  ];
  console.log(`[geo] npx ${args.join(" ")}`);
  execFileSync("npx", args, { stdio: "inherit" });

  const bytes = fs.readFileSync(OUT);
  const gzipped = gzipSync(bytes, { level: 9 }).byteLength;
  console.log(
    `[geo] ${path.relative(dashboardDir, OUT)}: ${bytes.byteLength} B raw · ${gzipped} B gzipped ` +
      `(budget 1,572,864 B) · simplify ${SIMPLIFY_PERCENTAGE} · quantization ${QUANTIZATION}`,
  );
  if (gzipped > 1_572_864) throw new Error("topojson is over the 1.5 MB gzipped budget");
}

main().catch((error) => {
  console.error(`[geo] FAILED: ${error.message}`);
  process.exit(1);
});
