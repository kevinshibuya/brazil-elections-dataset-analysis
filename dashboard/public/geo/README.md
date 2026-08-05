# `br-municipalities.topo.json`

The choropleth's only geometry. Committed (unlike `public/data/`, which the
exporter writes and `.gitignore` excludes) because it does not change between
election runs and because the page must work on a clean checkout.

|  |  |
| --- | --- |
| Source | IBGE malhas territoriais, API v3, municipal intra-region, `qualidade=intermediaria` — the **same** URL and fetch date as `data/fixtures/mun_centroids.csv` |
| Fetched | 2026-08-04 |
| Features | 5 570 municipalities (the 2022 electoral universe) + 27 dissolved UF outlines |
| Key | **TSE** municipality code (`properties.code`), re-keyed from IBGE codes through `data/fixtures/mun_crosswalk_tse_ibge.csv`. `properties.uf` rides along. |
| Simplification | mapshaper **0.6.111**, Visvalingam weighted, **`percentage=45%`** (retains ~150 k of the source's 546 k points), `keep-shapes` |
| Quantization | **`2e4`** — Brazil's bbox is ~44° wide, so one step is ~0.0022° ≈ 240 m |
| Measured size | **1,881,629 B raw · 472,506 B gzipped** (budget 1,572,864 B = 1.5 MB gzipped). Gzip measured with node `zlib.gzipSync({level: 9})` — what `scripts/build-geo.mjs` prints and enforces; CLI `gzip -9` gives 474,369 B. |

## Reproducing it

```sh
cd dashboard && node scripts/build-geo.mjs
```

That script is the authority: it holds the source URL, the crosswalk join, the
tolerance, and the quantization as named constants, and it fails rather than
dropping a municipality whose IBGE code is not in the crosswalk. The mapshaper
invocation it runs is, verbatim:

```sh
npx -y mapshaper@0.6.111 tse.geojson \
  -simplify visvalingam weighted percentage=45% keep-shapes \
  -filter-fields code,uf \
  -rename-layers municipalities \
  -dissolve2 fields=uf copy-fields= name=states + \
  -o format=topojson quantization=2e4 target=municipalities,states \
     public/geo/br-municipalities.topo.json
```

`-simplify` runs **before** `-dissolve2` on purpose: the state outlines are then
built from the very same arcs as the municipality borders, so the two layers
cannot drift apart. mapshaper reports 7 unrepaired self-intersections at this
tolerance; they are sub-pixel at national scale and SVG's nonzero fill rule
renders them without artefacts.

## What is deliberately NOT here

- **No exterior ("ZZ") geometry.** TSE's exterior municipalities are embassies
  and consulates, not places; `mun_centroids.csv` omits them for the same
  reason and the page keeps them off the map.
- **No tiles, no external hosts.** The page fetches this one file from its own
  origin and projects it in the browser. There is no runtime dependency on any
  map service.
