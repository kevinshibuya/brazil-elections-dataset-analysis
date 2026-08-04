# Measured baselines ledger

Every numeric budget in the plan gets a real measurement recorded here, with the task that measured it — a budget without a measured value is not a budget.

| Budget | Measured value | Measured at (task) | Date | Notes |
| --- | --- | --- | --- | --- |
| Quarantine rate ≤ 0.01 % | **0.000000 %** (0 of 6,721,734 rows attempted) | 5 (RR) | 2026-08-04 | Zero rows quarantined on the whole RR run, including the national BR members. Re-measure nationally at Task 23. |
| Reconciliation tolerance 0 | **PASS** — 0 fatal mismatches over 10,257 compared keys | 5 (RR) | 2026-08-04 | nominal 8,402 keys · legenda 1,135 · turnout 450 (90 × 5 metrics) · internal blank/null 180 (90 × 2). Findings, non-fatal: 147 `annulled_candidacy` rows / 27,255 votes and 21 `rollup_disagreement` rows. |
| Ingest wall clock (RR) | **312 s** cold / **237 s** warm | 5 (RR) | 2026-08-04 | 6.72 M rows attempted, 229 k loaded → 21.5–28.3 k rows/s attempted. 79 % of it is scanning `votacao_secao_2022_BR.csv` (5.38 M rows) for RR's 13.4 k presidential rows — the A2 tax on every single-UF run. |
| Reconciliation gate wall clock (RR) | **0.7 s** | 5 (RR) | 2026-08-04 | Five server-side comparisons; nothing is pulled into Python. |
| Local disk footprint | **RR: 1.03 GiB raw + 49.7 MiB Postgres.** National extrapolation **≈ 28.5 GiB vs 22 GiB free — OVER BUDGET** | 5 (RR) | 2026-08-04 | Arithmetic below. Escalated: the Task 23 free-disk pre-flight will block as things stand. |
| fact_votes row count | RR **211,087** → national extrapolation **≈ 100 M** | 5 (RR) | 2026-08-04 | 166.5 votável-rows per section in RR; see the bias note below. |

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
