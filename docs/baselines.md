# Measured baselines ledger

Every numeric budget in the plan gets a real measurement recorded here, with the task that measured it — a budget without a measured value is not a budget.

| Budget | Measured value | Measured at (task) | Date | Notes |
| --- | --- | --- | --- | --- |
| Quarantine rate ≤ 0.01 % | **0.000000 %** (0 of 6,721,734 rows attempted) | 5 (RR) | 2026-08-04 | Zero rows quarantined on the whole RR run, including the national BR members. Re-measure nationally at Task 23. |
| Reconciliation tolerance 0 | **PASS** — 0 fatal mismatches over 10,257 compared keys | 5 (RR) | 2026-08-04 | nominal 8,402 keys · legenda 1,135 · turnout 450 (90 × 5 metrics) · internal blank/null 180 (90 × 2). Findings, non-fatal: 147 `annulled_candidacy` rows / 27,255 votes and 21 `rollup_disagreement` rows. |
| Ingest wall clock (RR) | **312 s** cold / **237 s** warm | 5 (RR) | 2026-08-04 | 6.72 M rows attempted, 229 k loaded → 21.5–28.3 k rows/s attempted. 79 % of it is scanning `votacao_secao_2022_BR.csv` (5.38 M rows) for RR's 13.4 k presidential rows — the A2 tax on every single-UF run. |
| Reconciliation gate wall clock (RR) | **0.7 s** | 5 (RR) | 2026-08-04 | Five server-side comparisons; nothing is pulled into Python. |
| Reconciliation gate wall clock (2 UFs) | **8.9 s** over 3,864 sections (AC + AP) | 23 | 2026-08-05 | 12.7 × the RR figure for 3.05 × the sections — **superlinear**, and the append driver runs the gate again after *every* member. Escalated: see "Per-UF reconciliation cost" below. |
| Local disk footprint | **RR: 1.03 GiB raw + 49.7 MiB Postgres.** National extrapolation **≈ 28.5 GiB vs 22 GiB free — OVER BUDGET** | 5 (RR) | 2026-08-04 | Arithmetic below. Escalated: the Task 23 free-disk pre-flight will block as things stand. |
| fact_votes row count | RR **211,087** → national extrapolation **≈ 100 M** | 5 (RR) | 2026-08-04 | 166.5 votável-rows per section in RR; see the bias note below. |
| Shard size ≤ 300 KiB gz target / 1 MiB hard | **max 187,097 B** (Boa Vista, 741 sections) · **p50 12,977 B** — no municipality split | 8 (RR) | 2026-08-04 | 15 files, 371,749 B for the whole state. Distribution below. Re-measure at Task 23 (São Paulo capital is the worst case). |
| Overview topojson ≤ 1.5 MB gzipped (1,572,864 B) | **472,506 B gzipped** (1,881,629 B raw) — **30 % of budget** | 19 | 2026-08-04 | `dashboard/public/geo/br-municipalities.topo.json`. 5,570 municipalities + 27 dissolved UF outlines, ~150 k points. mapshaper 0.6.111, Visvalingam weighted `percentage=45%` `keep-shapes`, `quantization=2e4`. Measured with node `zlib.gzipSync({level:9})`, which is what `scripts/build-geo.mjs` prints; CLI `gzip -9` gives 474,369 B and `vite preview` serves it at 485,767 B. Election-independent, so this does NOT change at Task 23. |
| Overview first-load transfer | **597,312 B** over 7 requests | 19 (RR) | 2026-08-04 | Measured below. |

## Overview first-load transfer (2026-08-04, `vite preview` + Playwright chromium, RR export)

Cold load of `/` (the overview route) at 1280 × 1000, `waitUntil: networkidle`, summed
`transferSize` over the navigation plus every resource — i.e. compressed bytes on the wire,
headers included. Time to the first bound polygon: **848 ms** on localhost.

| Resource | Transfer (B) |
| --- | ---: |
| `geo/br-municipalities.topo.json` | 486,067 |
| `assets/index-*.js` | 100,730 |
| `assets/index-*.css` | 5,936 |
| `data/data-manifest.json` | 1,860 |
| `data/municipalities-index.*.json` | 1,020 |
| `data/summary.*.json` | 983 |
| `index.html` (navigation) | 716 |
| **Total** | **597,312** |

**81 % of the page is the map geometry**, and it is the one part that will not grow at
Task 23 — the boundaries are the same 5,570 municipalities whichever UFs the export
covers. What does grow is `municipalities-index.json`: 1,020 B here for RR's 15
municipalities, so on the order of 250–350 KB gzipped for all 5,570 (columnar, and the
`code`/`name`/`uf` columns compress well). Budget the national overview at roughly
0.85–0.95 MB first load and re-measure it at Task 23.

The topology is fetched, never imported, so it is not in the JS bundle and only routes
that draw a map pay for it.

## Two-UF append acceptance (2026-08-05, Task 23)

The first real evidence that a UF-by-UF national pass keeps what it loads.
`02_ingest.py --uf AC` (resetting) then `02_ingest.py --uf AP --append`, both as
real subprocesses against real archives, into `elections_test`.
`pipeline/tests/test_two_uf_append.py`, **703 s** for the whole file (two loads
plus a deliberate third that must fail).

| | AC | AP | both |
| --- | ---: | ---: | ---: |
| `dim_section` loaded | 2,124 | 1,740 | 3,864 |
| TSE `QT_TOTAL_SECOES` | 2,280 | 1,837 | 4,117 |
| loaded / published | 93.2 % | 94.7 % | 93.9 % |
| `fact_votes` rows | 300,301 | 309,533 | 609,834 |
| votes | 2,698,415 | 2,611,929 | 5,310,344 |
| `pg_database_size` | | | 124 MB |

Election ids `544`, `545`, `546` — neither state had a state runoff.
`quarantine`: 0 rows. Reconciliation: **PASS**, 23,324 keys compared, findings
only (`annulled_candidacy` 74 AC / 50 AP, `rollup_disagreement` 20 AC / 47 AP).

**The quarantine gate is scored per RUN, not over the warehouse** — the two
`meta.gate_runs` stamps read `attempted=6,799,345` and `attempted=6,807,206`,
i.e. each load is judged on its own ~6.8 M attempted rows (dominated by the A2
scan of the national `_BR` members, which every member pays). A cumulative
denominator would have read ~13.6 M on the second and would silently widen the
0.01 % gate with every state added. It does not.

## Per-UF reconciliation cost under the append driver — ESCALATED (2026-08-05, Task 23)

**Not in any budget, and it is hours.** The reconciliation gate runs over the
WHOLE warehouse after every member, so a 28-member pass runs it 28 times against
a warehouse that grows each time.

| Warehouse | Sections | Gate wall clock |
| --- | ---: | ---: |
| RR | 1,268 | 0.7 s |
| AC + AP | 3,864 | 8.9 s |

Two points do not pin the exponent, and they disagree badly about it: scaled
linearly from RR one national gate run is ~4.6 min, scaled linearly from the
two-UF figure it is ~19 min, and the two-UF figure is 12.7 × the RR one for
3.05 × the data. Summed over 28 members with a linearly growing warehouse that
is roughly **1–5 hours of pure reconciliation**, on top of the loads.

What it buys is real and should not be discarded casually: a state that loads
badly is caught at member 3 instead of member 28. The levers, in order of how
much they give up — run the gate every N members instead of every one; run it
only on the final member and accept that a bad load surfaces at the end; leave
it as is and budget the hours. **Measure one full-warehouse gate run before
choosing**; the extrapolation above is too loose to decide on.

## RR row counts (2026-08-04, `make pipeline UF=RR`)

| Table | Rows |
| --- | --- |
| `fact_votes` | 211,087 |
| `fact_section_stats` | 7,608 |
| `rollup_cand_munzona` | 8,816 |
| `rollup_party_munzona` | 1,200 |
| `rollup_detail_munzona` | 96 |
| `dim_section` | 1,268 |
| `dim_candidate` | 570 |
| `dim_municipality` | 15 |
| `quarantine` | 0 |

Elections present: `544` (PRES R1), `545` (PRES R2), `546` (state R1). No `547` — Roraima had no state runoff.

## RR shard size distribution (2026-08-04, `make export UF=RR`)

Gzipped bytes on disk, one file per municipality — `dashboard/public/data/shards/{code}.json.gz`.
Six race ids in every shard (`PRES_1`, `PRES_2`, `GOV_1`, `SEN_1`, `DEPFED_1`, `DEPEST_1`);
Roraima had no state runoff, so no `GOV_2`.

| Code | Municipality | Sections | Zones | gz bytes |
| --- | --- | ---: | ---: | ---: |
| 03018 | BOA VISTA | 741 | 2 | 187,097 |
| 03085 | RORAINÓPOLIS | 80 | 1 | 21,133 |
| 03069 | CANTÁ | 53 | 1 | 18,747 |
| 03034 | CARACARAÍ | 55 | 1 | 17,549 |
| 03093 | MUCAJAÍ | 44 | 1 | 16,331 |
| 03050 | ALTO ALEGRE | 43 | 1 | 15,577 |
| 03077 | BONFIM | 41 | 1 | 14,380 |
| 03123 | PACARAIMA | 36 | 1 | 12,977 |
| 03042 | AMAJARI | 27 | 1 | 11,195 |
| 03115 | NORMANDIA | 30 | 1 | 10,568 |
| 03026 | IRACEMA | 23 | 1 | 9,939 |
| 03000 | CAROEBE | 26 | 1 | 9,820 |
| 03158 | SÃO LUIZ | 22 | 1 | 9,294 |
| 03131 | SÃO JOÃO DA BALIZA | 21 | 1 | 8,911 |
| 03107 | UIRAMUTÃ | 26 | 1 | 8,231 |

**p50 12,977 B · p90 21,133 B · max 187,097 B · total 371,749 B over 15 files.**
Budget: 307,200 B (300 KiB) target, 1,048,576 B (1 MiB) hard. **Nothing split** — Boa
Vista, the state's only multi-zone municipality and 58 % of its sections, lands at 61 %
of the split threshold. Export wall clock: **1.2 s** for the whole state (five queries,
grouped in Python).

Rough national extrapolation, on the same basis as the storage section below
(× 357.8 sections, × 1.32 for RR's narrow ballot): **≈ 176 MB of shards over ≈ 5,570
municipality files** — comfortably inside the ≤ 20,000-file and ≤ 25 MB/file budgets, but
the per-file budget is decided by the *largest* municipality, not the mean. São Paulo
capital carries ~19,000 sections (≈ 26 × Boa Vista) across ~50 zones, so it will split;
whether each of its zones clears 1 MiB is the real question and is measured at Task 23.

## Storage baseline and national extrapolation

**Measured, RR only.**

| Item | Bytes | |
| --- | --- | --- |
| `data/raw/` total | 1,110,327,953 | 1.03 GiB |
| — national archives (needed by *every* run) | 1,100,499,452 | `detalhe_votacao_secao` 243.8 MB · `votacao_candidato_munzona` 556.1 MB · `votacao_secao_2022_BR` 271.3 MB · `votacao_partido_munzona` 24.6 MB · `detalhe_votacao_munzona` 4.4 MB · `modelourna` 0.3 MB |
| — RR-only archives | 9,828,501 | `votacao_secao_2022_RR` 7.7 MB + 4 correspondence zips 2.1 MB |
| Postgres `elections` database | 52,067,351 | 49.7 MiB (`pg_database_size`) |
| Postgres docker volume `…_elections-pgdata` | ~420 MB | whole cluster: `elections` + `elections_test` + templates + WAL. The ~370 MB above the database is WAL and system catalogs — bounded by `max_wal_size`, so it does **not** scale with the data. |

**Extrapolation basis.** TSE's own published section count, `QT_TOTAL_SECOES` in
`detalhe_votacao_munzona_2022` (PRES round 1, non-transit, all 28 UF members incl. ZZ):

```
national sections      496,238
RR sections              1,387
scale factor       496,238 / 1,387 = 357.8×
```

**Rows-per-section bias (known, one-directional).** RR carries 211,087 fact rows over
1,268 loaded sections = 166.5 votável-rows per section. RR runs one of the smallest
candidate fields in the country; states like SP run several times more DEPEST/DEPFED
candidates per ballot, so a pure section-count scale-up *under*-estimates. Cross-check:
`211,087 × 357.8 = 75.5 M` fact rows vs the spec's ~100 M expectation → a **× 1.32**
correction factor, applied to everything that scales with votável-rows rather than
sections.

```
Postgres (national)   49.7 MiB × 357.8 × 1.32  ≈ 23.0 GiB   (+ ~0.4 GiB cluster overhead)
raw, national files                             = 1.03 GiB   (fixed — already downloaded)
raw, 27 per-UF votacao_secao  7.7 MB × 357.8 × 1.32 ≈ 3.4 GiB
raw, 54 correspondence zips   2.1 MB × 357.8       ≈ 0.7 GiB  (per-section records; Task 17 only)
                                              ------------
TOTAL national footprint                        ≈ 28.5 GiB
```

**Against free disk: 22 GiB available** (`df -g /`, 2026-08-04; the plan recorded ~29 GiB
at authoring time, so the machine has lost headroom since). The budget requires the
footprint to fit with **≥ 20 % headroom**, i.e. ~34 GiB free for a 28.5 GiB run.

> **Over budget — escalated to the orchestrator (plan defect, not a Task 5 defect).**
> The Task 23 free-disk pre-flight will refuse to start as things stand. The levers, none
> of which Task 5 is authorised to pull: run the national load UF-by-UF and export +
> drop each state before the next (the exports, not the warehouse, are the deliverable);
> skip the correspondence archives unless Task 17 runs; move `data/raw/` or the Postgres
> volume to external storage; or free ~12 GiB on the host.

Note the extrapolation is first-order. The `× 1.32` correction is anchored to the spec's
own national row estimate, not to a measurement — the first real national number lands at
Task 23, which is exactly why the pre-flight exists.
