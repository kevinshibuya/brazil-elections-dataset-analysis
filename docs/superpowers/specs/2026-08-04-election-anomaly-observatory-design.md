# 2022 Brazilian Election Anomaly Observatory — Design

**Date:** 2026-08-04
**Status:** Approved design, pending implementation plan

## Purpose

A statistically rigorous, publicly publishable system that detects and contextualizes
anomalies in the 2022 Brazilian election results at the voting-section (urna) level,
across all races nationwide. Inspired by viral "300×0" claims — the system's job is to
evaluate such cases against proper local baselines, neutrally: findings are presented
with the context that explains them or fails to explain them.

**Stance:** neutral forensic observatory. Never "fraud finder", never debunking-only.
Every flag carries its local baseline, sample size, and score/p-value. Methodology
caveats (especially for digit tests) are presented front and center.

## Constraints (user-set)

- **Zero cost to publish.** The public site is fully static — no backend, no database
  in production. Free-tier hosting (Cloudflare Pages primary target: 20k-file,
  25 MB/file limits).
- **Drill-down without a backend** via pre-sharded static JSON (see Export & Sharding).
- **Bilingual** pt-BR (default) / English. All public copy authored by gpt-5.6-sol
  (codex CLI leg) during implementation.
- **Stack:** Python owns statistics; product layer is TypeScript/React/Tailwind.
  Postgres is the local source of truth. No NestJS API (dropped — sharding covers
  drill-down for both public and local use).
- 2022 data is frozen → run-once re-runnable batch pipeline. No cron, queues, or
  incremental updates.

## Architecture

```
TSE open data (CSVs, ~10–20 GB, ~100M section-level rows)
        │  1. INGEST (Python)
        ▼
Local Postgres — curated section-level tables, all races, both rounds
        │  2. ANALYZE (Python: polars + scipy) → analysis.* result tables
        ▼
        │  3. EXPORT (Python) — validation-gated
        ▼
Static JSON: aggregates + 5,570 municipality shards
        │  4. DASHBOARD (React + TS + Tailwind + Vite, static build)
        ▼
Public site (free hosting), bilingual
```

Components: `pipeline/` (Python, uv-managed, numbered scripts `01_download` →
`02_ingest` → `03_analyze` → `04_export`), `dashboard/` (Vite + React + TS +
Tailwind), `data/` (`raw/` git-ignored, `fixtures/` committed), `docs/`.

## Data model & ingestion

**Sources** (TSE `resultados-2022` open-data portal):

1. *Votação por seção eleitoral* — one row per (section × race × candidate/party).
   27 state files (state races) + national files (president). Core table.
2. *Detalhe da apuração por seção* — per (section × race): aptos, comparecimento,
   abstentions, blank, null.
3. *Municipality/zone rollups* — used to cross-validate our aggregations against
   TSE's official totals.

**Curated Postgres schema:**

- `dim_municipality` — code, name, state, region; IBGE join key reserved (not
  populated in v1).
- `dim_section` — (state, municipality, zone, section) + registered voters per round.
- `dim_candidate` — number, name, party, race, coalition.
- `fact_votes` — (section_id, race, round, candidate_id, votes); ~100M rows,
  partitioned by race.
- `fact_section_stats` — (section_id, race, round, aptos, comparecimento,
  abstentions, blank, null).

**Ingestion rules:**

- Raw zips downloaded once to `data/raw/`, checksummed, never modified; pipeline
  re-runnable from scratch with one command.
- TSE quirks (latin-1, semicolon-separated, `#NULO#`, `-1` sentinels) normalized at
  ingestion, documented in one module.
- **Reconciliation gate:** after every load, our aggregated totals must equal TSE's
  published municipality totals per race. Mismatch = hard failure. This is the
  standing defense against "they loaded the data wrong".
- No silent row-dropping: unparseable rows land in a `quarantine` table with a
  reason; > 0.01% quarantined = hard failure.
- Loads are atomic: staging → validate → swap.

## Anomaly analysis suite

Each detector writes per-section scores plus a national summary to `analysis.*`.

**Tier 1 — hard integrity checks (violations unambiguous):**

1. **Arithmetic consistency** — nominal + blank + null = comparecimento;
   comparecimento ≤ aptos; no negative counts.
2. **Cross-race presence** — a section reporting one race must report all races
   valid for its state.

**Tier 2 — per-section statistical outliers (graded, never binary):**

3. **Extreme-dominance sections** ("300×0" done right) — probability of a result at
   least this extreme given the municipality's vote share, via beta-binomial
   (tolerates natural overdispersion). Output: ranked list with local context.
4. **Cross-race split-ticket divergence** — per section, presidential-candidate
   share vs that candidate's coalition-party share in deputy races; divergence
   measured against the municipality's own split-ticket distribution. Correctly
   un-flags regional-alliance patterns (e.g. PL deputies + Lula in Maranhão) while
   catching genuinely extreme combinations.
5. **Turnout outliers** — per-section turnout vs municipality baseline, robust
   z-scores (MAD), both extremes flagged.

**Tier 3 — distribution-level forensics:**

6. **Turnout × vote-share fingerprint** (Klimek et al. 2012) — 2D density per
   state; ballot-stuffing signature is a smear toward the (100%, 100%) corner.
7. **Digit tests** — second-digit Benford + last-digit uniformity, per state and
   municipality-size stratum. Presented as screening only, with Deckert et al.
   (2011) caveats explicit.
8. **Urna-model comparison** — replicate the 2022 controversy's central claim
   properly: compare pre-2020 vs 2020 urna models *within* municipalities
   (the raw national gap is a geography artifact). Urna model per section comes
   from TSE correspondence files.

**Composite:** per-section score vector (one per detector) + combined percentile +
**clustering check** — do high-scoring sections concentrate in zones/municipalities
beyond chance?

## Export & sharding

One Python export step writes to `dashboard/public/data/`.

**A. Aggregates (eager-loaded):** `summary.json`; `states/{uf}.json` (aggregates,
fingerprint data, digit tests); `anomalies/{detector}.json` (top-500 ranked with
context); `municipalities-index.json` (id, name, UF, centroid, headline shares —
search + choropleth).

**B. Shards (lazy-loaded):** `shards/{municipality_code}.json.gz` — every section's
per-race results + anomaly score vector.

**Sharding care (explicit requirements):**

1. **Compact columnar encoding** — arrays, no repeated keys; candidates referenced
   by number against a per-shard lookup.
2. **Size budget enforced by exporter** — target ≤ 300 KB gzipped/shard, hard fail
   at 1 MB. Oversized municipalities (e.g. São Paulo capital) auto-split into
   per-zone subshards behind an index; dashboard resolves transparently.
3. **Schema versioning** — every file carries `{"v": N}`; dashboard refuses
   mismatches.
4. **Export validation gate** — re-read every shard: parses, version ok, totals
   reconcile against Postgres, size within budget, file count/total bytes within
   hosting limits. Any failure → loud export failure.
5. **Immutable + content-hashed via manifest** — aggressive CDN caching, no stale
   mixed deploys.

## Dashboard

React + TypeScript + Tailwind + Vite, static build. Recharts for charts, topojson
choropleth for maps. React Query as fetch/cache layer for shards.

Pages:

- **Overview** — national maps + headline findings.
- **Detector pages** (one per analysis) — methodology explainer + rankings + charts.
- **Municipality drill-down** — shard-powered section table, filters, per-section
  anomaly panel.
- **Methodology** — honest-caveats page with citations; load-bearing for
  credibility.

Bilingual: pt-BR default, EN toggle; copy authored by gpt-5.6-sol.

## Testing & verification

- **Detectors:** pytest on synthetic fixtures with known answers (planted outlier
  must rank #1; clean fixture yields no flags).
- **Parsers:** tests over committed real-file excerpts covering TSE quirks.
- **Reconciliation** doubles as the end-to-end integration test on real data.
- **Exporter:** validation gate + round-trip tests (export → parse → totals match
  Postgres).
- **Dashboard:** Vitest for shard decoding + i18n; headless browser smoke (loads,
  renders, zero console errors) per QA gate.

## Out of scope (v1)

- Comparison against 2018/2020 elections (future work).
- Demographic joins via IBGE (column reserved only).
- Any live backend or API.
- Real-time/incremental data updates.
