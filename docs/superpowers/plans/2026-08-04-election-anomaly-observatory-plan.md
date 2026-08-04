# 2022 Election Anomaly Observatory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Tasks use checkbox (`- [ ]`) syntax for tracking. Each task below is a slice contract per `~/.claude/templates/slice-contract.md`: the acceptance test is authored here (RED), is read-only to the implementer, and the Files list is a hard boundary.

**Spec:** `docs/superpowers/specs/2026-08-04-election-anomaly-observatory-design.md` (approved, authoritative).

**Goal:** A run-once-re-runnable Python pipeline (TSE CSVs → Postgres → anomaly analysis → validated static JSON shards) plus a bilingual static React dashboard, publishable at zero cost.

**Architecture:** Four numbered pipeline stages (`01_download → 02_ingest → 03_analyze → 04_export`) over local Postgres; hard gates (reconciliation, quarantine ≤ 0.01%, export validation) are acceptance tests, not docs. Dashboard is a Vite static build consuming aggregates eagerly and per-municipality gzipped columnar shards lazily. The pipeline is end-to-end testable on one small state (**Roraima, UF=RR** — the smallest electorate) from Task 5 onward; all-Brazil scale-out is a late, measured slice.

**Tech Stack:** Python 3.12 + uv, polars, scipy, psycopg (3), Postgres 16 (docker compose), pytest. Dashboard: Vite + React + TypeScript + Tailwind, TanStack React Query v5, Recharts, topojson, Vitest, Playwright (smoke only).

## Global Constraints

Copied from the spec — every task's requirements implicitly include these:

- Public site is **fully static**: no backend, no database, no API in production.
- Cloudflare Pages limits: **≤ 20,000 files, ≤ 25 MB per file**.
- Shard budget: **target ≤ 300 KB gzipped per shard, hard fail at 1 MB**; oversized municipalities auto-split into per-zone subshards behind an index.
- Quarantine gate: **> 0.01 % quarantined rows = hard failure**. No silent row-dropping.
- Reconciliation gate: our aggregates must **equal** TSE published municipality totals per race — tolerance **zero**, mismatch = hard failure.
- Every exported file carries `{"v": N}`; dashboard **refuses** version mismatches.
- Exports immutable + content-hashed via a manifest.
- Bilingual pt-BR (default) / EN. **All public copy is authored by gpt-5.6-sol via the codex-implementation skill** (Tasks 21–22), never by the implementing Claude agent.
- Python owns statistics; TypeScript/React owns product. No NestJS/API.
- `data/raw/` git-ignored; `data/fixtures/` committed.
- Neutral forensic stance in all copy and UI: every flag shows local baseline, sample size, and score/p-value; digit-test caveats front and center.
- TypeScript: no `any`.
- Out of scope (v1): 2018/2020 comparisons, IBGE joins (column reserved only), live backend, incremental updates.

## Numeric budgets & measured baselines

Per pipeline rules, every numeric budget carries a measured baseline. Nothing is measurable today (fresh repo, no data), so each baseline is measured at a named early slice and recorded in `docs/baselines.md`. **If a measurement lands on the wrong side of its budget, that is a plan defect — stop and escalate to the orchestrator; do not silently adjust the budget.**

| Budget | Value | Baseline measured at | Recorded in |
|---|---|---|---|
| Quarantine rate | ≤ 0.01 % | Task 5 (RR real ingest), re-measured Task 23 (national) | `docs/baselines.md` |
| Shard size | ≤ 300 KB gz target / 1 MB hard | Task 8 (RR shards), re-measured Task 23 (worst case: São Paulo capital) | `docs/baselines.md` |
| Hosting file count | ≤ 20,000 files | Task 10 gate counts; measured for real at Task 23 (expected ≈ 5,600 shards + aggregates + build assets) | `docs/baselines.md` |
| Max file size | ≤ 25 MB | Task 10 gate; measured Task 23 | `docs/baselines.md` |
| Reconciliation tolerance | 0 (exact) | Task 5 (RR), Task 23 (national) — gate pass IS the measurement | gate output |
| fact_votes row count | ~100 M expected (spec) | Task 23 | `docs/baselines.md` |
| Local disk footprint (raw zips + Postgres volume) | must fit free disk with ≥ 20 % headroom — **measured 2026-08-04: ~29 GiB free on this machine** | Task 5: RR raw bytes + per-row Postgres footprint, extrapolated by national section count; a free-disk pre-flight gates the start of Task 23 | `docs/baselines.md` |
| Overview topojson map asset | ≤ 1.5 MB gzipped (target) | Task 19 | `docs/baselines.md` |
| Deployed directory (`dist/` incl. `data/`) | ≤ 20,000 files, ≤ 25 MB/file — the WHOLE build, not just data exports | Task 24 post-build check (`check-limits.mjs`) | `docs/baselines.md` |

Storage decisions, binding and made now because the disk budget is tight: raw **zips are the only raw artifacts kept** — CSVs are never extracted to disk, parsers stream directly from the archives; staging tables are **UNLOGGED** and dropped immediately after each swap.

## File structure

```
pipeline/                     # uv project, package "observatory"
  pyproject.toml
  01_download.py … 04_export.py   # thin numbered wrappers over the package
  src/observatory/
    config.py                 # DATABASE_URL, paths, UF filter, constants
    sources.py                # TSE dataset URL manifest + checksums
    download.py
    parse.py                  # ALL TSE quirks live here (latin-1, ';', #NULO#, sentinels); streams from zips
    db.py                     # connection, schema DDL, staging→swap helpers
    ingest.py                 # staging → validate → swap; quarantine
    reconcile.py              # hard gate
    detectors/                # base.py + one module per detector
    export/
      shards.py               # columnar encoder + zone-split
      aggregates.py           # summary/states/anomalies/index
      manifest.py             # content hashing
      validate.py             # export validation gate
  tests/                      # pytest; integration tests marked @integration
data/
  raw/                        # git-ignored
  fixtures/                   # committed real-file excerpts + golden shards
dashboard/                    # Vite + React + TS + Tailwind
  src/
    data/                     # manifest loader, shard decoder, React Query hooks
    i18n/                     # pt-BR.json, en.json, methodology.{pt,en}.md
    pages/                    # Overview, Detector, Municipality, Methodology
    components/
  scripts/smoke.mjs           # Playwright headless smoke
  public/data/                # exporter output target (git-ignored)
docs/baselines.md             # measured baselines ledger
docker-compose.yml            # postgres:16
Makefile                      # pipeline UF=RR | pipeline-all | test-* | smoke
```

## Task → Rung table (proposal — orchestrator re-judges at dispatch)

| # | Task | Rung | Why |
|---|---|---|---|
| 1 | Repo scaffold + tooling | implementer-opus-medium | Well-specified, only local decisions |
| 2 | Download layer + checksums | implementer-opus-medium | Clear contract, single surface |
| 3 | TSE parser + quirks + real fixtures | integrator-opus-high | Real-data judgment, fixture curation |
| 4 | Schema + atomic ingest + quarantine | integrator-opus-high | Multi-table design, atomicity |
| 5 | Reconciliation gate (RR e2e) | integrator-opus-high | Hard gate, cross-source semantics |
| 6 | Detector framework + Tier 1 checks | integrator-opus-high | Framework design decision |
| 7 | Extreme-dominance (beta-binomial) | integrator-opus-high | Formula shipped; careful vectorized impl |
| 8 | Shard encoder + zone-split (RR) | integrator-opus-high | Schema shipped; split logic delegated |
| 9 | Aggregates export | implementer-opus-medium | Enumerated outputs, local decisions |
| 10 | Export validation gate + manifest | integrator-opus-high | Hard gate, hashing + budgets |
| 11 | Dashboard shell + data layer + decoder | integrator-opus-high | Cross-language contract, rendered surface |
| 12 | Municipality drill-down page | integrator-opus-high | User-facing, taste ≥ 7 |
| 13 | Split-ticket divergence detector | integrator-opus-high | Coalition semantics, real judgment |
| 14 | Turnout MAD outliers | implementer-opus-medium | Fully-specified statistic |
| 15 | Klimek fingerprint | implementer-opus-medium | Specified binning + export |
| 16 | Digit tests | implementer-opus-medium | Specified tests + strata |
| 17 | Urna-model comparison | integrator-opus-high | New source ingest + within-mun design |
| 18 | Composite score + clustering | integrator-opus-high | Cross-detector integration |
| 19 | Overview page (choropleth) | integrator-opus-high | User-facing, taste |
| 20 | Detector pages | integrator-opus-high | User-facing, taste |
| 21 | Methodology page structure | implementer-opus-medium | Structure only; copy is Task 22 |
| 22 | Bilingual copy (pt-BR + EN) | **codex-implementation (gpt-5.6-sol)** | Spec mandates sol authors all public copy |
| 23 | All-Brazil scale-out + baselines | integrator-opus-high | Perf/budget surprises likely |
| 24 | Cloudflare Pages deploy config | implementer-opus-medium | Mechanical config; real-site check via codex-computer-use |

No slice routes to editor-opus-low: nothing here is mechanically decided in advance, and no deep-reasoner slice is needed — the two statistically hard shapes (beta-binomial scoring, clustering test) are reasoned through and shipped in this plan. If Task 7's leave-one-out vectorization or Task 18's clustering turns out deeper than contracted, escalate that slice to deep-reasoner-opus-xhigh rather than improvising.

## Verification table

`PGURL` = `postgresql://postgres:postgres@localhost:5432/elections` (docker compose up first). `uv`/pytest commands run from `pipeline/`, npm commands from `dashboard/`, and **`make` targets run at the repo root** — the only Makefile lives there. Integration tests are deselected by default (`-m "not integration"` in pytest addopts) and run explicitly where listed.

| Task | Verify commands |
|---|---|
| 1 | `uv run pytest -q` · `npm run typecheck` · `npm run test -- --run` · `npm run smoke` |
| 2 | `uv run pytest tests/test_download.py -v` |
| 3 | `uv run pytest tests/test_parse.py -v` |
| 4 | `uv run pytest tests/test_ingest.py -v` (needs Postgres up) |
| 5 | `make pipeline UF=RR` then `uv run pytest tests/test_reconcile.py -v -m integration` |
| 6 | `uv run pytest tests/test_tier1.py -v` |
| 7 | `uv run pytest tests/test_extreme_dominance.py -v` |
| 8 | `uv run pytest tests/test_shards.py -v` then RR export run + size report |
| 9 | `uv run pytest tests/test_aggregates.py -v` |
| 10 | `uv run pytest tests/test_export_gate.py -v` then full RR export with gate |
| 11 | `npm run typecheck` · `npm run test -- --run` · `npm run smoke` |
| 12 | `npm run typecheck` · `npm run test -- --run` · `npm run smoke` (drill-down route) |
| 13 | `uv run pytest tests/test_split_ticket.py -v` |
| 14 | `uv run pytest tests/test_turnout.py -v` |
| 15 | `uv run pytest tests/test_fingerprint.py -v` |
| 16 | `uv run pytest tests/test_digits.py -v` |
| 17 | `uv run pytest tests/test_urna_model.py -v` |
| 18 | `uv run pytest tests/test_composite.py -v` |
| 19 | `npm run typecheck` · `npm run test -- --run` · `npm run smoke` (overview route) |
| 20 | `npm run typecheck` · `npm run test -- --run` · `npm run smoke` (each detector route) |
| 21 | `npm run typecheck` · `npm run test -- --run` · `npm run smoke` (methodology route) |
| 22 | `npm run test -- --run` (i18n completeness test) · `npm run smoke` in both locales |
| 23 | `make pipeline-all` · `uv run pytest -m integration -v` (reconciliation + export gate national) · baselines recorded |
| 24 | `npm run build` · `node scripts/check-limits.mjs dist` (file count/size over the exact deploy dir) · `npm run smoke` against `vite preview` of the production build · codex-computer-use pass on the deployed preview URL |

Browser truth: every dashboard slice (11, 12, 19, 20, 21, 22, 24) verifies with the headless smoke in addition to typecheck/vitest — static checks are blind to runtime module-resolution and data-loading failures.

**Execution notes.** Execute on a `feat/election-anomaly-observatory` branch cut from `plan/election-anomaly-observatory` (or from main only after the plan branch has been merged there) — the executing session needs this plan file, including the review-wave fixes, in its working tree. Never commit to main. Each task ends with a commit. Before Task 1: the single three-leg plan-review wave, then one consolidated fix pass. Detector slices 13–18 are mutually independent (parallelizable after 6); dashboard slices 19–21 are independent of detectors 13–18 except where their data files are inputs — run detector slice before its page slice, or point the page at fixture data.

---

### Task 1 — Repo scaffold + tooling
**Spec TODO:** Components: `pipeline/` (Python, uv-managed, numbered scripts), `dashboard/` (Vite + React + TS + Tailwind), `data/` (`raw/` git-ignored, `fixtures/` committed), `docs/`.

**Files**
- `pipeline/pyproject.toml`, `pipeline/src/observatory/__init__.py`, `pipeline/src/observatory/config.py`, `pipeline/tests/test_scaffold.py` — create
- `dashboard/` — create: Vite React-TS app with Tailwind, Vitest, Playwright dev-dep, `scripts/smoke.mjs`, `src/App.tsx` placeholder shell
- `docker-compose.yml`, `Makefile`, `.gitignore`, `docs/baselines.md` (empty ledger with table header) — create

**Work**
Intent: a contributor with Docker + uv + node runs `docker compose up -d`, `make test-pipeline`, `make test-dashboard`, `make smoke` green in one sitting. Binding constraints: Python 3.12, uv-managed; pytest `addopts = -m "not integration"` with `integration` and `db` markers registered; `config.py` reads `DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/elections`) and `DATA_DIR`; `.gitignore` covers `data/raw/`, `dashboard/public/data/`, `node_modules`, `.venv`; Tailwind + strict TS (`"strict": true`, no `any`); `npm run smoke` is self-orchestrating: it builds, starts `vite preview`, runs Playwright chromium against it, asserts `#root` has rendered children and **zero console errors**, tears the preview down, and exits non-zero on any failure — so the verification tables' standalone `npm run smoke` invocations just work; `make smoke` merely delegates to it. Makefile targets (repo root): `pipeline UF=…`, `pipeline-all`, `test-pipeline`, `test-dashboard`, `smoke` (later tasks fill pipeline targets; stub them now with a clear "not implemented" failure). Internal structure otherwise your choice; report deviations.

**Acceptance check** — `pipeline/tests/test_scaffold.py`, authored here, RED until scaffold exists:
```python
from pathlib import Path

def test_config_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from observatory.config import settings
    assert settings.database_url.startswith("postgresql://")
    assert settings.data_dir.name == "data"

def test_raw_dir_gitignored():
    root = Path(__file__).resolve().parents[2]
    gi = (root / ".gitignore").read_text()
    assert "data/raw" in gi and "public/data" in gi
```
Run: `cd pipeline && uv run pytest tests/test_scaffold.py -v`. You make it pass; you never edit it.

**Verify before returning:** table row 1 (all four commands).

**Boundaries:** No TSE logic, no schema DDL, no real pages. Out of scope: CI config, deploy config (Task 24). `blocked:` on any ambiguity for your rung.

---

### Task 2 — Download layer: sources manifest + checksums
**Spec TODO:** Raw zips downloaded once to `data/raw/`, checksummed, never modified; pipeline re-runnable from scratch with one command.

**Files**
- `pipeline/src/observatory/sources.py`, `pipeline/src/observatory/download.py`, `pipeline/01_download.py`, `pipeline/tests/test_download.py` (test pre-authored) — create
- `Makefile` — modify: wire `pipeline` target's download step

**Interfaces (produced):** `sources.datasets(uf: str | None) -> list[Dataset]` where `Dataset(name, url: str | None, kind, uf, resolver: str | None = None)` and kinds are `votes_section` (per-UF), `detail_section` (**one national file — the per-UF detalhe pattern 404s**; `uf=None`), `rollup_cand_munzona`, `rollup_party_munzona`, `rollup_detail_munzona` (all national), `urna_correspondence` (per-UF per-round; `url=None, resolver="ckan"`, resolved at fetch time) plus the static national `modelourna_numerointerno`. `download.fetch_all(uf: str | None) -> None` idempotent into `data/raw/`; `download.py` exposes a module-level `RAW_DIR` (initialized from `config`) that tests monkeypatch.

**Work**
Intent: one command fetches exactly the needed TSE zips, verifies/records SHA-256 in `data/raw/checksums.json`, never re-downloads a file whose checksum matches, never mutates existing raw files. `--uf RR` narrows the per-UF votes files; the national files are always needed — even a RR run downloads ~800 MB of national detail + rollup files (that is the floor, not a bug). Verified sources (probed live 2026-08-04; re-verify at implementation, report drift): `https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/votacao_secao_2022_{UF}.zip`; `…/odsele/detalhe_votacao_secao/detalhe_votacao_secao_2022.zip` (national only, ~244 MB); `…/odsele/votacao_candidato_munzona/votacao_candidato_munzona_2022.zip`; `…/odsele/votacao_partido_munzona/votacao_partido_munzona_2022.zip`; `…/odsele/detalhe_votacao_munzona/detalhe_votacao_munzona_2022.zip`; `…/odsele/modelo_urna/modelourna_numerointerno.zip`. Urna correspondence: the CKAN datasets `resultados-2022-correspondencias-esperadas-e-efetivadas-{1,2}-turno` publish per-UF files with unpredictable timestamps in the name (`…/eleicoes/eleicoes2022/correspesp/CESP_{t}t_{UF}_{timestamp}.zip`), so `fetch_all` resolves those URLs at run time via the CKAN API (`https://dadosabertos.tse.jus.br/api/3/action/package_show`) — never a hardcoded pattern. Binding (disk budget): downloaded zips are never extracted; downstream readers stream from the archives. Tests never hit the network: fake fetcher injected. HTTP plumbing your choice.

**Acceptance check** — `pipeline/tests/test_download.py`:
```python
from observatory import sources

def test_rr_subset_includes_national_files():
    ds = sources.datasets("RR")
    kinds = {d.kind for d in ds}
    assert {"votes_section", "detail_section", "rollup_cand_munzona",
            "rollup_party_munzona", "rollup_detail_munzona"} <= kinds
    assert any(d.uf == "RR" for d in ds if d.kind == "votes_section")
    assert all(d.uf is None for d in ds if d.kind == "detail_section")  # national-only file
    assert all(d.url.startswith("https://cdn.tse.jus.br/") for d in ds if d.url is not None)
    assert all(d.resolver == "ckan" for d in ds if d.url is None)       # correspondence resolved at fetch time

def test_idempotent_skip(tmp_path, monkeypatch):
    from observatory import download
    calls = []
    def fake_fetch(url, dest): calls.append(url); dest.write_bytes(b"zipbytes")
    monkeypatch.setattr(download, "_fetch", fake_fetch)
    monkeypatch.setattr(download, "RAW_DIR", tmp_path)
    download.fetch_all("RR"); n = len(calls)
    download.fetch_all("RR")
    assert len(calls) == n  # checksummed files not re-fetched
```
Run: `cd pipeline && uv run pytest tests/test_download.py -v`.

**Verify:** table row 2. **Boundaries:** no parsing, no DB. If the CKAN resolution of the correspondence files fails at fetch time, download the rest and return `blocked:` naming exactly that dataset (Task 17 depends on it).

---

### Task 3 — TSE parser + quirk normalization + committed real excerpts
**Spec TODO:** TSE quirks (latin-1, semicolon-separated, `#NULO#`, `-1` sentinels) normalized at ingestion, documented in one module.

**Files**
- `pipeline/src/observatory/parse.py`, `pipeline/tests/test_parse.py` (pre-authored) — create
- `data/fixtures/tse/` — create: real-file excerpts (see Work)
- `pipeline/tests/conftest.py` — create: fixture-path helpers
- `pipeline/tests/__init__.py` — create: makes `from tests.conftest import fixture` importable

**Interfaces (produced):** every reader returns `(good: pl.DataFrame, bad: pl.DataFrame)` where `bad` has `raw_line, reason`. `parse.read_votes(src)` → `uf, mun_code, mun_name, zone, section, race, round, votavel_kind, cand_number, cand_name, party_number, votes`. **`votavel_kind`** ∈ {`candidate`,`blank`,`null`,`legenda`}: NR_VOTAVEL 95 → `blank`, 96 → `null`, a 2-digit NR_VOTAVEL in proportional races (DEPFED/DEPEST/DEPDIS) → `legenda` (it IS the party number), else `candidate`. `party_number` (int) = the 2-digit prefix of the candidate number for candidates, the number itself for legenda, NULL for blank/null — the votação file carries **no party-sigla column** and NM_VOTAVEL is the only name field. `parse.read_detail(src)` → `uf, mun_code, zone, section, race, round, aptos, comparecimento, abstentions, blank, null_votes, agg_secoes` (`agg_secoes` = number of sections aggregated into this one, NULL/0 when none — verify the real header's aggregation column names and report what actually exists). `parse.read_rollup_cand(src)`, `read_rollup_party(src)`, `read_rollup_detail(src)` mirror their files with the same `(good, bad)` shape. `race` ∈ {`PRES`,`GOV`,`SEN`,`DEPFED`,`DEPEST`,`DEPDIS`} mapped from CD_CARGO (1,3,5,6,7,8); `round` ∈ {1,2}. **Canonical `mun_code` is the TSE code zero-padded to 5 chars, as TEXT, from here through the DB, exports, and URLs.** Sentinel semantics (binding, per column class): in count columns `-1`/`-3` mean "not available" → normalized to NULL, never 0; a NULL or negative `votes` in `read_votes` sends the row to `bad`; NULLs in detail columns are allowed and treated as missing downstream. `src` may point inside a zip — readers stream from archives, never require extraction.

**Work**
First download RR for real (`uv run python 01_download.py --uf RR`), then cut committed excerpts: for each file kind, the header line + ~200 representative rows + hand-picked rows exhibiting each quirk (latin-1 accents, `#NULO#`/`#NULO`, `-1` and `-3` sentinels, quoted semicolons if present, legenda and blank/null votável rows, at least one aggregated section — seções agregadas — in the detalhe excerpt, and ZZ/exterior rows from the national president file). Excerpts must remain byte-identical to source lines (latin-1, not re-encoded) — they are the regression net for "they loaded the data wrong". Document every quirk you actually observe as a comment block at the top of `parse.py`; that module is the single home for quirk knowledge. Column-name constants from TSE headers (`SG_UF`, `CD_MUNICIPIO`, `NR_ZONA`, `NR_SECAO`, `CD_CARGO`, `NR_TURNO`, `NR_VOTAVEL`/`NR_CANDIDATO`, `QT_VOTOS`, …) — verify against the real header, don't trust this list blindly. Parsing engine (polars `read_csv` with encoding vs manual) is your choice.

**Acceptance check** — `pipeline/tests/test_parse.py` (representative core; the committed excerpts make these exact):
```python
import polars as pl
from observatory import parse
from tests.conftest import fixture

def test_votes_excerpt_parses_clean():
    good, bad = parse.read_votes(fixture("tse/votacao_secao_2022_RR_excerpt.csv"))
    assert bad.height == 0
    assert set(good["race"].unique()) <= {"PRES","GOV","SEN","DEPFED","DEPEST","DEPDIS"}
    assert set(good["votavel_kind"].unique()) <= {"candidate","blank","null","legenda"}
    assert good.filter(pl.col("votavel_kind") == "legenda").height > 0   # excerpt includes legenda rows
    assert good["votes"].min() >= 0                      # sentinels never leak into counts
    assert (good["mun_code"].str.len_chars() == 5).all() # canonical zero-padded TEXT code
    assert good.filter(pl.col("cand_name") == "#NULO#").height == 0

def test_detail_excerpt_parses_clean():
    good, bad = parse.read_detail(fixture("tse/detalhe_votacao_secao_2022_excerpt.csv"))
    assert bad.height == 0
    assert {"null_votes", "agg_secoes"} <= set(good.columns)
    assert good.filter(pl.col("agg_secoes") > 0).height >= 1   # excerpt exercises an aggregated section
    assert (good["aptos"].drop_nulls() >= 0).all()

def test_malformed_row_goes_to_bad_not_dropped():
    good, bad = parse.read_votes(fixture("tse/votacao_malformed_excerpt.csv"))
    assert bad.height >= 1 and "reason" in bad.columns
```
Run: `cd pipeline && uv run pytest tests/test_parse.py -v`.

**Verify:** table row 3. **Boundaries:** no DB writes. The three rollup readers (`read_rollup_cand`/`read_rollup_party`/`read_rollup_detail`) are included here — same module, same `(good, bad)` contract shape. Excerpt curation is judgment — that's why this is integrator-rung.

---

### Task 4 — Postgres schema + atomic ingest + quarantine gate
**Spec TODO:** Loads are atomic: staging → validate → swap. No silent row-dropping: unparseable rows land in a `quarantine` table with a reason; > 0.01 % quarantined = hard failure.

**Files**
- `pipeline/src/observatory/db.py`, `pipeline/src/observatory/ingest.py`, `pipeline/02_ingest.py`, `pipeline/tests/test_ingest.py` (pre-authored) — create
- `Makefile` — modify: ingest step

**Interfaces (produced):** Tables: `dim_municipality(code TEXT PK zero-padded, name, uf, region, ibge_code NULL)`, `dim_section(id PK, uf, mun_code FK, zone, section, aptos_r1, aptos_r2, agg_secoes NULL, UNIQUE(uf,mun_code,zone,section))`, `dim_candidate(id PK, uf, race, round, number, name, party_number, coalition NULL, UNIQUE(uf, race, round, number))` — deputy numbers repeat across states, so uniqueness MUST include uf; national races use `uf='BR'`; ingest hard-asserts that no (uf, race, round, number) maps to two candidate names. `fact_votes(section_id, race, round, votavel_kind, candidate_id NULL, party_number NULL, votes)` partitioned by race — `candidate_id` set only for `votavel_kind='candidate'`, `party_number` set for `candidate`/`legenda`. `fact_section_stats(section_id, race, round, aptos, comparecimento, abstentions, blank, null_votes)`, `quarantine(source_file, raw_line, reason, at)`, rollup mirrors `rollup_cand_munzona`, `rollup_party_munzona`, `rollup_detail_munzona`, and `meta.gate_runs(gate, ok, detail, at)`. Staging tables are UNLOGGED and dropped right after the swap (disk budget). `ingest.run(uf: str | None) -> IngestReport(rows_attempted, rows_loaded, rows_quarantined, quarantine_rate)`; raises `QuarantineGateError` when rate > 0.0001.

**Work**
Intent: `02_ingest.py --uf RR` streams every raw zip through Task 3's readers into `staging.*` tables, validates (FK integrity, quarantine rate), then swaps into place in one transaction (rename or partition attach — your choice; the observable contract is that a failed run leaves the previous good state untouched). `bad` rows from parsers → `quarantine` with reason. The 0.01 % gate compares quarantined vs total attempted rows across the run. Re-running from scratch (`make pipeline UF=RR`) must be deterministic. Indexing/copy strategy (COPY vs polars write) your choice — 100 M rows nationally, so COPY-based is strongly suggested; report your throughput on RR.

**Acceptance check** — `pipeline/tests/test_ingest.py` (uses live test DB `elections_test`, marker `db`):
```python
import pytest, polars as pl
from observatory import ingest

pytestmark = pytest.mark.db

def test_atomic_swap_preserves_previous_state(test_db, fixture_batch_ok, fixture_batch_poison):
    ingest.load_batch(test_db, fixture_batch_ok)          # good load
    before = ingest.counts(test_db)
    with pytest.raises(ingest.QuarantineGateError):
        ingest.load_batch(test_db, fixture_batch_poison)  # >0.01% bad rows
    assert ingest.counts(test_db) == before               # swap never happened

def test_quarantine_rows_carry_reason(test_db, fixture_batch_one_bad):
    # fixture contract: >= 10,000 attempted rows with exactly one bad -> stays under the 0.01% gate
    report = ingest.load_batch(test_db, fixture_batch_one_bad)
    assert report.rows_attempted >= 10_000
    assert report.rows_quarantined == 1
    q = ingest.read_quarantine(test_db)
    assert q.height == 1 and q["reason"][0] != ""
```
`test_db` / `fixture_batch_*` fixtures: implementer writes these in `conftest.py` (synthetic small batches built from Task 3 excerpt data — building test fixtures is yours; the assertions are not). Run: `cd pipeline && uv run pytest tests/test_ingest.py -v`.

**Verify:** table row 4 (Postgres up). **Boundaries:** no reconciliation logic (Task 5), no analysis schema. `load_batch` granularity vs whole-run `run()` internal split is your choice as long as both exist and `run()` composes `load_batch`.

---

### Task 5 — Reconciliation gate + RR end-to-end
**Spec TODO:** Reconciliation gate: after every load, our aggregated totals must equal TSE's published municipality totals per race. Mismatch = hard failure.

**Files**
- `pipeline/src/observatory/reconcile.py`, `pipeline/tests/test_reconcile.py` (pre-authored) — create
- `pipeline/02_ingest.py`, `Makefile` — modify: gate runs automatically after every load
- `docs/baselines.md` — modify: record RR quarantine rate, row counts, ingest wall-clock, and the storage baseline (RR raw-zip bytes + Postgres volume footprint, extrapolated by national section count — this is what the Task 23 free-disk pre-flight checks against)

**Work**
Gate contract (load-bearing, binding) — **three category-scoped comparisons**, tolerance zero on each (one flat candidate-keyed comparison is structurally impossible: the candidato munzona file carries neither blank/null/legenda votes nor turnout):
1. **Nominal:** `fact_votes[votavel_kind='candidate']` grouped by `(mun_code, race, round, candidate number)` vs `rollup_cand_munzona` aggregated over zones.
2. **Legenda:** `fact_votes[votavel_kind='legenda']` grouped by `(mun_code, race, round, party_number)` vs `rollup_party_munzona` legenda totals.
3. **Turnout/blank/null:** `fact_section_stats` sums per `(mun_code, race, round)` vs `rollup_detail_munzona` (aptos, comparecimento, abstentions, blank, null_votes).

Plus one internal cross-check: blank/null rows in `fact_votes` must sum to `fact_section_stats` blank/null per (mun_code, race, round). ZZ (exterior) municipalities are in scope wherever the rollup files carry them. Every mismatch row → `reconciliation_report(mun_code, race, round, category, key, ours, theirs, diff)`; any mismatch → `ReconciliationError` after writing the full report (never fail on first diff — the report is the diagnostic). Success and failure both stamp `meta.gate_runs`. This gate doubles as the e2e integration test: the acceptance run is the real RR pipeline.

**Acceptance check** — `pipeline/tests/test_reconcile.py`:
```python
import pytest
from observatory import reconcile

def test_mismatch_writes_full_report_then_raises(test_db, seeded_mismatch):
    with pytest.raises(reconcile.ReconciliationError):
        reconcile.run_gate(test_db)
    rep = reconcile.read_report(test_db)
    assert rep.height == seeded_mismatch.expected_diffs   # ALL diffs, not just first
    assert {"category", "ours", "theirs", "diff"} <= set(rep.columns)

@pytest.mark.integration
def test_rr_real_data_reconciles(real_db):
    assert reconcile.run_gate(real_db).ok    # zero mismatches on real RR ingest
```
Run: `make pipeline UF=RR` (repo root), then `cd pipeline && uv run pytest tests/test_reconcile.py -v -m integration` (plus the unit test un-marked).

**Verify:** table row 5. Also: record baselines (budget table) in `docs/baselines.md`. **Boundaries:** no detector work. If real RR data does NOT reconcile after honest investigation of our side, return `blocked:` with the report excerpt — a genuine TSE-side discrepancy is a plan-level finding, not something to paper over with tolerance.

---

### Task 6 — Detector framework + Tier 1 integrity checks
**Spec TODO:** Tier 1 — arithmetic consistency; cross-race presence. Each detector writes per-section scores plus a national summary to `analysis.*`.

**Files**
- `pipeline/src/observatory/detectors/base.py`, `detectors/__init__.py` (registry), `detectors/tier1.py`, `pipeline/03_analyze.py`, `pipeline/tests/test_tier1.py` (pre-authored) — create
- `pipeline/src/observatory/db.py`, `Makefile` — modify: analysis schema DDL, analyze step

**Interfaces (produced — load-bearing, all later detectors conform):**
```sql
CREATE TABLE analysis.section_scores (
  section_id BIGINT NOT NULL, race TEXT NOT NULL, round SMALLINT NOT NULL,
  detector TEXT NOT NULL, score DOUBLE PRECISION NOT NULL, detail JSONB,
  PRIMARY KEY (detector, section_id, race, round));
CREATE TABLE analysis.detector_summary (
  detector TEXT PRIMARY KEY, sections_scored BIGINT, sections_flagged BIGINT,
  params JSONB, summary JSONB, at TIMESTAMPTZ);
CREATE TABLE analysis.state_payloads (           -- persisted from DetectorResult.state_payloads
  detector TEXT NOT NULL, uf TEXT NOT NULL, payload JSONB NOT NULL,
  PRIMARY KEY (detector, uf));
```
Score convention: **higher = more anomalous**; probabilistic detectors store `score = -log10(max(p, 1e-16))`; `detail` carries the human context (baseline, n, tails). Race/round conventions (binding): race-specific detectors write their real `(race, round)`; cross-race detectors write `race='ALL'` with a real `round`. Python side: `class Detector(Protocol): name: str; def run(self, db) -> DetectorResult` where `DetectorResult(summary: dict, state_payloads: dict[str, dict] | None = None)` — DB-only detectors return `DetectorResult({})`; the framework persists `state_payloads` into `analysis.state_payloads`. Registration is explicit in `detectors/__init__.py` (`detectors.all()`), and **every later detector slice lists that file in its Files**. `03_analyze.py --only <name>` runs a subset.

**Work**
Tier 1 checks, exactly: (a) arithmetic, per (section, race, round): **nominal + legenda + blank + null == comparecimento** (the legenda term is zero for majoritarian races — omitting it would flag essentially every DEPFED/DEPEST section); `comparecimento ≤ aptos`, EXCEPT sections with `agg_secoes > 0` (seções agregadas: their comparecimento legitimately includes voters from aggregated sections while aptos counts only their own) — those are exempt from the aptos rule with the exemption recorded in `detail`; no negative counts; rows with NULL (sentinel) inputs are excluded and counted in the summary, never flagged. Violations score 1.0 per violated rule (detail lists rules), clean sections 0. Writes real (race, round). (b) cross-race presence (`race='ALL'`, per round): the expected (uf, round) → races matrix is **derived from the ingested data itself** — the set of races present in that UF's files for that round — never hardcoded (2022 round 2 has GOV in only 12 states, and ZZ/exterior is PRES-only; the derivation handles both for free). Comparisons are within-round only, and the derived matrix is stamped into `detector_summary.summary` so it is inspectable. Missing races scored per missing race with detail. Framework internals (how detectors get polars frames, batching) your choice.

**Acceptance check** — `pipeline/tests/test_tier1.py` (synthetic frames built inline via a `seed_sections(...)` helper the implementer writes in conftest; assertions fixed):
```python
import pytest
import polars as pl
from observatory.detectors import tier1

pytestmark = pytest.mark.db

def test_arithmetic_violation_flagged(test_db, seed_sections):
    seed_sections(test_db, [dict(comp=100, blank=5, null=5, nominal=90, legenda=5, aptos=120)])  # 105≠100
    tier1.Arithmetic().run(test_db)
    scores = tier1.read_scores(test_db, "t1_arithmetic")
    assert scores.height == 1 and scores["score"][0] >= 1.0

def test_clean_section_with_legenda_scores_zero(test_db, seed_sections):
    seed_sections(test_db, [dict(comp=100, blank=5, null=5, nominal=85, legenda=5, aptos=120)])
    tier1.Arithmetic().run(test_db)
    assert tier1.read_scores(test_db, "t1_arithmetic")["score"].max() == 0.0

def test_aggregating_section_exempt_from_aptos_rule(test_db, seed_sections):
    # seções agregadas: comp > aptos is legitimate when agg_secoes > 0
    seed_sections(test_db, [dict(comp=350, blank=5, null=5, nominal=335, legenda=5,
                                 aptos=300, agg_secoes=2)])
    tier1.Arithmetic().run(test_db)
    assert tier1.read_scores(test_db, "t1_arithmetic")["score"].max() == 0.0

def test_missing_race_flagged_against_derived_matrix(test_db, seed_sections):
    # two sections, same UF/round: one reports PRES+GOV, one only PRES ->
    # the DERIVED matrix expects GOV, so exactly the second section is flagged
    seed_sections(test_db, [dict(races=["PRES", "GOV"]), dict(races=["PRES"])])
    tier1.CrossRacePresence().run(test_db)
    scores = tier1.read_scores(test_db, "t1_presence")
    assert scores.filter(pl.col("score") >= 1.0).height == 1
```
Run: `cd pipeline && uv run pytest tests/test_tier1.py -v`.

**Verify:** table row 6. **Boundaries:** no Tier 2/3 logic; no export awareness.

---

### Task 7 — Extreme-dominance detector (beta-binomial)
**Spec TODO:** Extreme-dominance sections ("300×0" done right) — probability of a result at least this extreme given the municipality's vote share, via beta-binomial.

**Files**
- `pipeline/src/observatory/detectors/extreme_dominance.py`, `pipeline/tests/test_extreme_dominance.py` (pre-authored) — create
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector

**Work**
The scoring shape is load-bearing and shipped; vectorization and DB plumbing are yours.

Scope: presidential races, rounds 1 and 2, every candidate with ≥ 1 % national vote share in that round (decided; extension to governor races is out of scope v1). For section *i* in municipality *m*: `k_i` votes for the candidate out of `n_i` nominal (valid candidate) votes in that race/round.

Baseline is **leave-one-out** per section (a 300×0 section in a small municipality must not launder itself into its own baseline): compute `p̂` and `ρ̂` over the municipality's other sections via sufficient-statistic subtraction, not a per-section refit loop.

```python
# ANOVA method-of-moments estimator (Ridout–Demétrio–Firth style), per
# (municipality, race, round, candidate), over sections j != i:
#   J = count, S1 = Σ n_j, S2 = Σ n_j², p̂ = Σ k_j / S1, s_j = k_j / n_j
#   num = Σ n_j (s_j - p̂)²  -  (J - 1) · p̂(1 - p̂)
#   den = p̂(1 - p̂) · (S1 - S2/S1 - (J - 1))
#   ρ̂  = clamp(num / den, 1e-6, 0.99)          # den <= 0 or p̂ in {0,1} → ρ̂ = 1e-6
# Fallback: if J < 5 after leave-one-out, use the state-level (p̂, ρ̂) for that
#   race/round/candidate and set detail["baseline"] = "state" (else "municipality").
from scipy.stats import betabinom

def tail_probs(k: int, n: int, p: float, rho: float) -> tuple[float, float]:
    a = p * (1 - rho) / rho
    b = (1 - p) * (1 - rho) / rho
    p_hi = float(betabinom.sf(k - 1, n, a, b))   # P(X >= k)
    p_lo = float(betabinom.cdf(k, n, a, b))      # P(X <= k)
    return p_hi, p_lo
```
Score = `-log10(max(min(p_hi, p_lo), 1e-16))`, written as `(race='PRES', round)` rows per the Task 6 conventions; sections with `n_i = 0` are not scored; per-section `detail` = `{cand, k, n, p_hat, rho_hat, p_hi, p_lo, baseline}`. Section-level score for the composite = max over scored candidates. Summary: national count with min-tail < 1e-4, and the top of the ranked list.

**Acceptance check** — `pipeline/tests/test_extreme_dominance.py`:
```python
import pytest
from observatory.detectors import extreme_dominance as ed

def test_planted_shutout_ranks_first(test_db, seed_votes):
    # 40 sections ~55% for cand 13 (n≈300 each), one section 300/300
    seed_votes(test_db, baseline_share=0.55, sections=40, n=300, planted=(300, 300))
    ed.ExtremeDominance().run(test_db)
    top = ed.read_scores(test_db).sort("score", descending=True)
    assert top["detail"][0]["k"] == 300 and top["detail"][0]["n"] == 300
    assert top["score"][0] > top["score"][1] * 3          # planted section dominates

def test_clean_overdispersed_municipality_no_extreme_flags(test_db, seed_votes):
    # natural spread: shares drawn 0.35–0.75 across sections — must NOT alarm
    seed_votes(test_db, share_range=(0.35, 0.75), sections=60, n=250, planted=None)
    ed.ExtremeDominance().run(test_db)
    assert ed.read_scores(test_db)["score"].max() < 4.0    # no p < 1e-4

def test_small_municipality_uses_state_fallback(test_db, seed_votes):
    seed_votes(test_db, sections=3, n=200, with_state_context=True)
    ed.ExtremeDominance().run(test_db)
    assert set(ed.read_scores(test_db)["detail"].struct.field("baseline").unique()) == {"state"}
```
(`seed_votes` fixture is the implementer's; RNG explicitly seeded — pin `seed=20221002` — so runs are deterministic.) Run: `cd pipeline && uv run pytest tests/test_extreme_dominance.py -v`.

**Verify:** table row 7. **Boundaries:** presidential only; no export changes. If the leave-one-out sufficient-statistic algebra doesn't hold together when implemented (it should — subtraction of `k_i, n_i, n_i²` from the sums), return `blocked:` rather than switching silently to a full-baseline fit.

---

### Task 8 — Shard encoder + per-zone auto-split (RR export)
**Spec TODO:** Compact columnar encoding; size budget enforced by exporter; oversized municipalities auto-split into per-zone subshards behind an index.

**Files**
- `pipeline/src/observatory/export/shards.py`, `pipeline/04_export.py`, `pipeline/tests/test_shards.py` (pre-authored) — create
- `data/fixtures/golden_shard/` — create: one Python-encoded RR shard committed as golden fixture for the TS decoder (Task 11); must contain at least one non-PRES race (e.g. `GOV_1`) so the cross-language round-trip exercises the race-id convention
- `docs/baselines.md` — modify: RR shard size distribution
- `Makefile` — modify: export step

**Work**
The shard schema is load-bearing and shipped verbatim (v = 1). Encoder internals your choice; output `dashboard/public/data/shards/{mun_code}.json.gz`.

```jsonc
// Regular shard — shards/{code}.json.gz   (code = zero-padded 5-char TSE municipality code)
{
  "v": 1, "code": "03018", "name": "Boa Vista", "uf": "RR",
  // Canonical race ids ALWAYS carry the round suffix. Full national vocabulary:
  // PRES_1, PRES_2, GOV_1, GOV_2 (the 12 runoff states), SEN_1, DEPFED_1, DEPEST_1, DEPDIS_1 (DF).
  "races": ["PRES_1", "PRES_2", "GOV_1", "SEN_1", "DEPFED_1", "DEPEST_1"],
  "cand": { "PRES_1": [[13, "LULA", 13], [22, "JAIR BOLSONARO", 22]] },  // [number, name, party_number]
  "zone":    [8, 8, 9],          // parallel arrays, one entry per section,
  "section": [1, 2, 1],          // sorted by (zone, section); index = section position
  "stats": { "PRES_1": { "aptos": [], "comp": [], "blank": [], "null_votes": [], "legenda": [] } },
        // dense, aligned; "legenda" all-zero for majoritarian races
  "votes": { "PRES_1": { "sec": [0,0,1], "cand": [13,22,13], "n": [201,102,188] } },
        // candidate (nominal) votes ONLY — sparse COO triplets sorted by (sec, cand),
        // zero-vote pairs omitted; blank/null/legenda totals live in "stats"
  // scores keyed by race id, plus reserved ALL_1/ALL_2 for cross-race detectors
  // (t1_presence, split_ticket, composite); dense per-section arrays, null = unscored
  "scores": { "PRES_1": { "extreme_dominance": [2.1, 0.3, null], "turnout": [0.4, 0.1, 0.2] },
              "ALL_1":  { "t1_presence": [0, 0, 0], "composite": [97.1, 41.0, null] } },
  "score_detail": { "PRES_1": { "0": { "extreme_dominance": { /* detail JSON from analysis */ } } } }
        // outer key = race id or ALL_r, inner key = section index; ONLY for sections at/above
        // the flag threshold (composite percentile >= 99, per Task 18) — keeps shards small
}
// Split index — written AT shards/{code}.json.gz when the regular encoding exceeds budget:
{ "v": 1, "split": true, "code": "71072", "name": "São Paulo", "uf": "SP", "zones": [1, 2, 3] }
// …with per-zone subshards at shards/{code}-z{zone}.json.gz using the regular schema
// restricted to that zone (plus "zone_only": <zone>).
```
Budget enforcement lives here: after encoding, if gzipped size > 300 KB → re-encode as split; if any subshard still > 1 MB → raise (hard fail per spec). Encoder must be a pure function `encode_shard(mun_data) -> dict` separable from file I/O (the golden fixture and round-trip tests need it). Declared writer contract: `write_shard(mun_data, out_dir: Path) -> WriteResult(paths: list[Path], index_bytes: bytes | None, gz_sizes: dict[str, int])` — `index_bytes` set only for split municipalities.

**Acceptance check** — `pipeline/tests/test_shards.py`:
```python
import gzip, json, pytest
from observatory.export import shards

def test_round_trip_totals_match_input(shard_input_fixture):
    doc = shards.encode_shard(shard_input_fixture)
    assert doc["v"] == 1
    total = sum(doc["votes"]["PRES_1"]["n"])
    assert total == shard_input_fixture.expected_pres1_total
    assert len(doc["zone"]) == len(doc["section"]) == len(doc["stats"]["PRES_1"]["aptos"])

def test_no_zero_vote_pairs_and_sorted(shard_input_fixture):
    v = shards.encode_shard(shard_input_fixture)["votes"]["PRES_1"]
    assert min(v["n"]) > 0
    assert v["sec"] == sorted(v["sec"])

def test_race_ids_canonical_and_score_keys_valid(shard_input_fixture):
    doc = shards.encode_shard(shard_input_fixture)
    assert all(r.rsplit("_", 1)[1] in {"1", "2"} for r in doc["races"])  # every id carries a round
    assert set(doc["scores"]) <= set(doc["races"]) | {"ALL_1", "ALL_2"}

def test_oversized_municipality_splits_by_zone(big_shard_input, tmp_path):   # synthetic >300KB gz
    out = shards.write_shard(big_shard_input, tmp_path)
    idx = json.loads(gzip.decompress(out.index_bytes))
    assert idx["split"] is True and len(idx["zones"]) >= 2

def test_subshard_over_1mb_hard_fails(giant_zone_input, tmp_path):
    with pytest.raises(shards.ShardBudgetError):
        shards.write_shard(giant_zone_input, tmp_path)
```
Run: `cd pipeline && uv run pytest tests/test_shards.py -v`, then real run: `uv run python 04_export.py --uf RR --shards-only` and record sizes in `docs/baselines.md`.

**Verify:** table row 8. **Boundaries:** aggregates files are Task 9; validation gate + manifest are Task 10. Schema field names are frozen — the TS decoder (Task 11) is written against them.

---

### Task 9 — Aggregates export
**Spec TODO:** Aggregates (eager-loaded): `summary.json`; `states/{uf}.json`; `anomalies/{detector}.json` (top-500 ranked with context); `municipalities-index.json`.

**Files**
- `pipeline/src/observatory/export/aggregates.py`, `pipeline/tests/test_aggregates.py` (pre-authored) — create
- `data/fixtures/mun_crosswalk_tse_ibge.csv`, `data/fixtures/mun_centroids.csv` — create: committed, sources noted in headers
- `pipeline/04_export.py` — modify: wire aggregates

**Work**
All files carry `"v": 1`. Contents (binding): `summary.json` — national totals per race/round, turnout, per-detector headline counts from `detector_summary`; `states/{uf}.json` — state totals per race, turnout, plus per-detector state payloads read generically from `analysis.state_payloads` (populated by the detector framework from `DetectorResult.state_payloads`) — later detector slices (15, 16, 17) need **no exporter edits**; `anomalies/{detector}.json` — national top-500 by score with context `{mun_code, mun_name, uf, zone, section, race, round, score, detail}`; `municipalities-index.json` — `[code, name, uf, lat, lon, headline_shares, flag_density]` arrays (columnar); `flag_density` = count of composite-high sections per municipality — exported as null until Task 18 lands, populated on the Task 18 re-export. Centroids/geometry: TSE and IBGE municipality codes are DIFFERENT code spaces (Boa Vista: TSE 03018, IBGE 1400100), so centroids come from IBGE data joined through the committed TSE↔IBGE crosswalk fixture (source noted; TSE's own municipality correspondence or the base-dos-dados crosswalk). This does not violate the spec's out-of-scope line — that excludes *demographic* IBGE joins, not the code crosswalk. ZZ (exterior) municipalities get null centroids: searchable, never mapped. Exact intra-file layout beyond this is your choice.

**Acceptance check** — `pipeline/tests/test_aggregates.py`:
```python
from observatory.export import aggregates

def test_anomalies_capped_ranked_with_context(analysis_seeded_db, tmp_out):
    aggregates.write_all(analysis_seeded_db, tmp_out)      # seeded with 600 scored sections
    doc = load_json(tmp_out / "anomalies" / "extreme_dominance.json")
    assert doc["v"] == 1 and len(doc["rows"]) == 500
    scores = [r["score"] for r in doc["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert {"mun_name", "uf", "detail"} <= set(doc["rows"][0])

def test_summary_totals_equal_db_totals(analysis_seeded_db, tmp_out):
    aggregates.write_all(analysis_seeded_db, tmp_out)
    doc = load_json(tmp_out / "summary.json")
    assert doc["races"]["PRES_2"]["total_votes"] == db_total(analysis_seeded_db, "PRES", 2)
```
Run: `cd pipeline && uv run pytest tests/test_aggregates.py -v`.

**Verify:** table row 9. **Boundaries:** no manifest/hashing (Task 10); detector-specific state payloads arrive later through `analysis.state_payloads`, not through exporter edits.

---

### Task 10 — Export validation gate + content-hash manifest
**Spec TODO:** Export validation gate — re-read every shard: parses, version ok, totals reconcile against Postgres, size within budget, file count/total bytes within hosting limits. Immutable + content-hashed via manifest.

**Files**
- `pipeline/src/observatory/export/manifest.py`, `export/validate.py`, `pipeline/tests/test_export_gate.py` (pre-authored) — create
- `pipeline/04_export.py` — modify: gate is the last, mandatory step

**Work**
Manifest shape (load-bearing, frozen — the dashboard data layer is written against it):
```jsonc
// data-manifest.json (the ONE mutable file; everything else immutable)
// "v" is the SINGLE version stamp — manifest and data schema version move together
{ "v": 1, "generated": "2026-08-04T12:00:00Z",
  "files": { "shards/03018.json.gz": { "path": "shards/03018.ab12cd34.json.gz",
                                        "sha256": "…", "bytes": 48213 },
             "summary.json": { "path": "summary.ab99ffee.json", "sha256": "…", "bytes": 3120 } },
  "count": 22, "bytes": 1048576 }
```
Physical files are written under their hashed names (first 8 hex of sha256 before the extension); logical names appear only in the manifest. Gate (binding, in order, ALL files re-read from disk): (1) parses; (2) `v == 1`; (3) shard nominal totals — per race, COO sums — equal Postgres `fact_votes[votavel_kind='candidate']` sums for that municipality, and stats arrays (incl. blank/null_votes/legenda) sum to their Postgres counterparts; (4) every shard ≤ 1 MB, warn-listing those > 300 KB; (5) `count ≤ 20_000`, every file ≤ 25 MB. Any failure → `ExportGateError` after a full failure report (all findings, not first). Gate pass stamps `meta.gate_runs`.

**Acceptance check** — `pipeline/tests/test_export_gate.py`:
```python
import pytest
from observatory.export import validate, manifest

def test_tampered_shard_fails_totals_check(exported_rr, test_db):
    tamper_one_vote(exported_rr)                          # helper: +1 to one COO entry
    with pytest.raises(validate.ExportGateError) as e:
        validate.run_gate(exported_rr, test_db)
    assert "totals" in str(e.value)

def test_manifest_covers_every_file_and_hashes_verify(exported_rr, test_db):
    validate.run_gate(exported_rr, test_db)
    m = manifest.load(exported_rr)
    on_disk = physical_files(exported_rr)
    assert {f["path"] for f in m["files"].values()} == on_disk - {"data-manifest.json"}
    assert manifest.verify_hashes(exported_rr)            # sha256 of each physical file

def test_version_mismatch_fails(exported_rr, test_db):
    rewrite_version(exported_rr, "summary.json", v=2)
    with pytest.raises(validate.ExportGateError):
        validate.run_gate(exported_rr, test_db)
```
Run: `cd pipeline && uv run pytest tests/test_export_gate.py -v`; then, from the repo root, `make pipeline UF=RR` must finish with the gate green.

**Verify:** table row 10. **Boundaries:** no dashboard code. The gate re-reads from disk — never validates in-memory objects it was handed.

---

### Task 11 — Dashboard shell: data layer, shard decoder, i18n scaffold
**Spec TODO:** Dashboard — React Query as fetch/cache layer for shards. Schema versioning — dashboard refuses mismatches. Bilingual: pt-BR default, EN toggle.

**Files**
- `dashboard/src/data/manifest.ts`, `src/data/shards.ts` (decoder), `src/data/hooks.ts`, `src/data/types.ts` — create
- `dashboard/src/i18n/` (`index.tsx`, `pt-BR.json`, `en.json` with placeholder keys), `src/App.tsx`, router + layout, locale toggle — create/modify
- `dashboard/src/data/shards.test.ts`, `src/i18n/i18n.test.ts` (pre-authored) — create
- `dashboard/public/data/` ← RR export output present locally (not committed)

**Interfaces (produced):** `loadManifest(): Promise<Manifest>` (fetched with `cache: "no-store"`); `resolve(logical: string): string` via manifest; `decodeShard(bytes: ArrayBuffer): Shard` — gunzips via `DecompressionStream("gzip")` (decided: hosting serves `.json.gz` as opaque bytes; the client decompresses — do not rely on CDN Content-Encoding) and parses into a typed `Shard` matching Task 8's schema exactly, resolving split indexes transparently (`useShard(munCode)` returns merged sections whether or not the municipality is split); throws `SchemaVersionError` when `v !== 1`, and the app surfaces a bilingual "please refresh" state. The `Shard` type mirrors Task 8's REVISED schema exactly: race ids always suffixed with the round, `scores`/`score_detail` keyed by race id plus reserved `ALL_1`/`ALL_2`, `stats` including `null_votes` and `legenda` arrays. i18n: `t(key)` over flat JSON per locale, pt-BR default, persisted toggle; ALL user-visible strings go through `t()` from day one — English/Portuguese placeholder values are fine until Task 22, hardcoded JSX strings are not.

**Work**
Internal structure (React Query setup, router layout, component split) is your choice. Placeholder pages for Overview / Detectors / Municipality / Methodology routes render headers only.

**Acceptance check** — `dashboard/src/data/shards.test.ts` (golden fixture from Task 8 copied into `dashboard/src/data/__fixtures__/`):
```ts
import { describe, it, expect } from "vitest";
import { decodeShard, SchemaVersionError } from "./shards";
import { goldenBytes, goldenExpected } from "./__fixtures__";  // Python-encoded RR shard + totals JSON

describe("shard decoder ↔ Python encoder", () => {
  it("round-trips the golden shard with matching totals", async () => {
    const shard = await decodeShard(goldenBytes);
    expect(shard.v).toBe(1);
    const total = shard.votes["PRES_1"].n.reduce((a, b) => a + b, 0);
    expect(total).toBe(goldenExpected.pres1Total);
    expect(shard.zone.length).toBe(shard.section.length);
    expect(shard.races.some((r) => !r.startsWith("PRES"))).toBe(true); // golden covers a non-PRES race
    for (const k of Object.keys(shard.scores))
      expect(shard.races.includes(k) || /^ALL_[12]$/.test(k)).toBe(true);
  });
  it("refuses schema version mismatch", async () => {
    await expect(decodeShard(withVersion(goldenBytes, 2))).rejects.toBeInstanceOf(SchemaVersionError);
  });
});
```
plus `i18n.test.ts`: `pt-BR.json` and `en.json` have identical key sets; default locale is pt-BR. Run: `cd dashboard && npm run test -- --run`.

**Verify:** table row 11 — typecheck, vitest, **and** `npm run smoke` (rendered surface). **Boundaries:** no real page content, no charts. Golden fixture is read-only — if it doesn't decode, the bug is on one side of the Task 8 schema; report, don't regenerate the fixture to fit.

---

### Task 12 — Municipality drill-down page
**Spec TODO:** Municipality drill-down — shard-powered section table, filters, per-section anomaly panel.

**Files**
- `dashboard/src/pages/Municipality/` (page, section table, filters, anomaly panel, search-by-index) — create
- `dashboard/src/pages/Municipality/municipality.test.tsx` (pre-authored) — create
- `dashboard/src/i18n/*.json` — modify: add keys (placeholder values)

**Work**
Intent: search a municipality (via `municipalities-index.json`), lazy-load its shard through `useShard`, render a virtualized/paginated section table (zone, section, per-race shares, per-detector score badges), filter by zone / race / min-score, click a section → anomaly panel showing each detector's score per race id (incl. the ALL_r cross-race entries) with its `score_detail` context (baseline share, n, tails) — the neutral-stance surface: every flag with its local baseline and sample size. Split municipalities must be indistinguishable to the user. UI composition and styling your choice (taste bar applies); all strings via `t()`.

**Acceptance check** — `municipality.test.tsx` (vitest + testing-library, fetch mocked to serve the golden RR fixture set):
```tsx
it("renders section rows from a decoded shard and filters by min score", async () => {
  renderWithProviders(<MunicipalityPage code="03018" />);
  expect(await screen.findAllByTestId("section-row")).not.toHaveLength(0);
  await user.click(screen.getByTestId("filter-min-score"));      // preset high threshold
  const rows = screen.getAllByTestId("section-row");
  for (const r of rows) expect(Number(r.dataset.maxScore)).toBeGreaterThanOrEqual(2);
});
it("anomaly panel shows baseline, n and score for a flagged section", async () => {
  renderWithProviders(<MunicipalityPage code="03018" />);
  await user.click((await screen.findAllByTestId("section-row"))[0]);
  const panel = screen.getByTestId("anomaly-panel");
  expect(panel).toHaveTextContent(/n\s*=/i);
  expect(panel.querySelectorAll("[data-testid=detector-score]").length).toBeGreaterThan(0);
});
```
Run: `cd dashboard && npm run test -- --run`.

**Verify:** table row 12 incl. smoke on the drill-down route with RR data in `public/data/`. **Boundaries:** no choropleth (Task 19), no detector methodology copy.

---

### Task 13 — Split-ticket divergence detector
**Spec TODO:** Cross-race split-ticket divergence … divergence measured against the municipality's own split-ticket distribution. Correctly un-flags regional-alliance patterns.

**Files**
- `pipeline/src/observatory/detectors/split_ticket.py`, `pipeline/tests/test_split_ticket.py` (pre-authored) — create
- `pipeline/src/observatory/coalitions.py` — create: the 2022 presidential coalition → party **numbers** as reviewed constants (round 1, both leading coalitions) — numbers, not siglas: the votação file carries only `party_number` (Task 3) — with the TSE source noted in comments
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector

**Work**
Per section, per presidential candidate c (top-2, round 1): `d_i = share_pres(c, i) − share_dep(coalition_parties(c), i)` where the deputy share is the summed federal-deputy vote share of c's coalition parties in that section. The reference distribution is the **municipality's own** `{d_j}` (this is what un-flags Maranhão-style alliances — the whole municipality shares the pattern, so no section deviates from it): robust z `= (d_i − median_m(d)) / (1.4826 · MAD_m(d))`, leave-one-out not required (medians are resistant), municipalities with < 10 sections fall back to state distribution with `detail.baseline = "state"`. Score `= |z| / 2` so z = 6 → score 3 (comparable scale to −log10 p detectors; noted in detector_summary.params), written as `(race='ALL', round=1)` per Task 6 conventions (the round-1 score also feeds both composite rounds — Task 18). Detail: `{cand, d, median, mad, z, baseline}`.

**Acceptance check** — `pipeline/tests/test_split_ticket.py`:
```python
def test_uniform_alliance_pattern_not_flagged(test_db, seed_split):
    # every section: pres share .70, coalition-deputy share .30 — big gap, but uniform
    seed_split(test_db, pres=0.70, dep=0.30, sections=50, noise=0.02)
    split_ticket.SplitTicket().run(test_db)
    assert read_scores(test_db, "split_ticket")["score"].max() < 2.0

def test_single_divergent_section_ranks_first(test_db, seed_split):
    seed_split(test_db, pres=0.55, dep=0.50, sections=50, noise=0.02,
               planted=dict(pres=0.98, dep=0.10))
    split_ticket.SplitTicket().run(test_db)
    top = read_scores(test_db, "split_ticket").sort("score", descending=True)
    assert top["detail"][0]["z"] > 5 and top["score"][0] > 2 * top["score"][1]
```
Run: `cd pipeline && uv run pytest tests/test_split_ticket.py -v`.

**Verify:** table row 13. **Boundaries:** coalition party lists are constants reviewed at plan review — if TSE coalition data in `dim_candidate` contradicts `coalitions.py`, report it; do not silently prefer either.

---

### Task 14 — Turnout outlier detector (robust z / MAD)
**Spec TODO:** Turnout outliers — per-section turnout vs municipality baseline, robust z-scores (MAD), both extremes flagged.

**Files**
- `pipeline/src/observatory/detectors/turnout.py`, `pipeline/tests/test_turnout.py` (pre-authored) — create
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector

**Work**
Fully specified: per (race=PRES, round) — turnout `t_i = comparecimento / aptos`; sections with `agg_secoes > 0` are excluded up front (seções agregadas make the aptos denominator wrong by construction; exclusion count goes in the summary); robust z against the municipality's sections `z = (t_i − median) / (1.4826 · MAD)`; both signs flagged; score `= |z| / 2`; `MAD == 0` → fall back to state distribution (`detail.baseline = "state"`); `< 10` sections → same fallback. Detail `{t, median, mad, z, baseline}`.

**Acceptance check** — `pipeline/tests/test_turnout.py`:
```python
def test_both_extremes_flagged(test_db, seed_turnout):
    seed_turnout(test_db, base=0.80, noise=0.02, sections=60,
                 planted=[0.999, 0.35])                     # one high, one low
    turnout.Turnout().run(test_db)
    top2 = read_scores(test_db, "turnout").sort("score", descending=True).head(2)
    zs = [d["z"] for d in top2["detail"]]
    assert max(zs) > 4 and min(zs) < -4

def test_zero_mad_falls_back_to_state(test_db, seed_turnout):
    seed_turnout(test_db, constant=0.80, sections=20, with_state_context=True)
    turnout.Turnout().run(test_db)
    assert "state" in set(read_scores(test_db, "turnout")["detail"].struct.field("baseline"))
```
Run: `cd pipeline && uv run pytest tests/test_turnout.py -v`. **Verify:** row 14. **Boundaries:** presidential only.

---

### Task 15 — Klimek turnout × vote-share fingerprint
**Spec TODO:** 2D density per state; ballot-stuffing signature is a smear toward the (100 %, 100 %) corner.

**Files**
- `pipeline/src/observatory/detectors/fingerprint.py`, `pipeline/tests/test_fingerprint.py` (pre-authored) — create
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector

**Work**
Fully specified: per (state, presidential round, leading candidate = national round winner): 2D histogram over sections, x = turnout %, y = candidate share of valid votes %, 100×100 grid of 1 % bins, raw counts (client renders log-scaled). Corner mass metric: fraction of sections with turnout > 90 % AND share > 90 %, reported in `detector_summary.summary` per state. This detector returns the per-state payload `{grid, corner_mass, n_sections}` via `DetectorResult.state_payloads` (the framework persists it to `analysis.state_payloads`; the exporter picks it up generically); per-section scores are NOT written (distribution-level detector — `sections_scored = 0`; composite ignores it).

**Acceptance check** — `pipeline/tests/test_fingerprint.py`:
```python
def test_grid_shape_and_mass_conservation(test_db, seed_state):
    seed_state(test_db, sections=500)
    r = fingerprint.Fingerprint().run(test_db)
    grid = r.state_payloads["RR"]["grid"]
    assert len(grid) == 100 and len(grid[0]) == 100
    assert sum(map(sum, grid)) == 500

def test_planted_corner_smear_raises_corner_mass(test_db, seed_state):
    seed_state(test_db, sections=500, corner_planted=50)   # turnout>95%, share>95%
    r = fingerprint.Fingerprint().run(test_db)
    assert r.state_payloads["RR"]["corner_mass"] >= 50 / 550
```
Run: `cd pipeline && uv run pytest tests/test_fingerprint.py -v`. **Verify:** row 15. **Boundaries:** no rendering (Task 20 draws it).

---

### Task 16 — Digit tests (second-digit Benford + last-digit uniformity)
**Spec TODO:** Digit tests per state and municipality-size stratum. Presented as screening only, Deckert et al. caveats explicit.

**Files**
- `pipeline/src/observatory/detectors/digits.py`, `pipeline/tests/test_digits.py` (pre-authored) — create
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector

**Work**
Fully specified: over section-level candidate vote counts (presidential, both rounds, top-2 candidates): (a) second-digit test on counts ≥ 10 — chi-square against Benford's second-digit distribution; (b) last-digit test on counts ≥ 100 — chi-square against uniform(0–9). Strata: per state × municipality-size bucket (`small < 20` sections, `mid 20–99`, `large ≥ 100`). Output per stratum: `{test, n, chi2, p, digit_freqs, expected_freqs}` via `DetectorResult.state_payloads` + national summary. Sparse strata (binding): a chi-square is computed only when the stratum has ≥ 100 eligible counts AND every expected bin count ≥ 5; otherwise the stratum is emitted as `{test, n, skipped: true, reason}` with no p-value — never NaN. No per-section scores (screening-level; `sections_scored = 0`). The Deckert/Mebane caveat text itself is Task 22 copy — this slice just exports the numbers.

**Acceptance check** — `pipeline/tests/test_digits.py`:
```python
def test_benford_conformant_counts_pass(test_db, seed_counts_benford):
    digits.Digits().run(test_db)
    for s in digits.read_strata(test_db):
        if s["test"] == "second_digit": assert s["p"] > 0.01

def test_planted_last_digit_bias_detected(test_db, seed_counts_last_digit_biased):
    # counts rounded to end in 0/5 far above uniform expectation
    digits.Digits().run(test_db)
    ld = [s for s in digits.read_strata(test_db) if s["test"] == "last_digit"]
    assert any(s["p"] < 1e-4 for s in ld)

def test_sparse_strata_skipped_not_nan(test_db, seed_counts_small):
    digits.Digits().run(test_db)                       # all counts < 10 -> nothing eligible
    strata = digits.read_strata(test_db)
    assert strata and all(s.get("skipped") and "p" not in s for s in strata)
```
Run: `cd pipeline && uv run pytest tests/test_digits.py -v`. **Verify:** row 16. **Boundaries:** numbers only; no caveat prose.

---

### Task 17 — Urna-model comparison
**Spec TODO:** Compare pre-2020 vs 2020 urna models *within* municipalities. Urna model per section comes from TSE correspondence files.

**Files**
- `pipeline/src/observatory/detectors/urna_model.py`, `pipeline/tests/test_urna_model.py` (pre-authored) — create
- `pipeline/src/observatory/parse.py`, `sources.py`, `ingest.py`, `db.py` — modify: correspondence-file reader; `dim_section.urna_model` column DDL (`pre2020` | `ue2020` | NULL) populated at ingest
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector

**Work**
Intent: replicate the 2022 controversy's claim properly. For each municipality containing BOTH model classes (map TSE model codes: UE2009/2010/2011/2013/2015 → `pre2020`, UE2020 → `ue2020`): within-municipality difference in Bolsonaro share (PRES round 2), Δ_m = share(pre2020 sections) − share(ue2020 sections), vote-weighted. National result: vote-weighted mean of Δ_m with a cluster-robust (municipality-level) 95 % CI, presented ALONGSIDE the raw national gap (the geography artifact) — both numbers exported so the dashboard can show the contrast. National + per-state numbers travel via `DetectorResult` (summary + state_payloads). Per-section scores: none (`sections_scored = 0`). Source (verified live 2026-08-04): the CKAN datasets `resultados-2022-correspondencias-esperadas-e-efetivadas-{1,2}-turno` → per-UF `CESP_{t}t_{UF}_{timestamp}.zip` files (section ↔ urna internal number), joined with the static `modelourna_numerointerno.zip` (internal number → urna model); Task 2 already resolves those URLs via the CKAN API. Correspondence-file quirks live in `parse.py` like all others. If the real files turn out not to support a per-section model assignment for round 2, return `blocked:` with what they DO contain.

**Acceptance check** — `pipeline/tests/test_urna_model.py`:
```python
def test_geography_artifact_vanishes_within_municipality(test_db, seed_two_towns):
    # Town A (pro-B .70) mostly old urnas; town B (pro-B .30) mostly new urnas;
    # WITHIN each town both models have identical shares.
    seed_two_towns(test_db)
    r = urna_model.UrnaModel().run(test_db)
    assert abs(r.summary["raw_gap"]) > 0.10               # naive gap looks scary
    assert abs(r.summary["within_mun_gap"]) < 0.01        # honest gap ~0
    assert r.summary["ci95"][0] < 0 < r.summary["ci95"][1]

def test_planted_true_model_effect_detected(test_db, seed_model_effect):
    seed_model_effect(test_db, effect=0.05)               # real within-town 5pt gap
    r = urna_model.UrnaModel().run(test_db)
    assert r.summary["within_mun_gap"] > 0.03 and r.summary["ci95"][0] > 0
```
Run: `cd pipeline && uv run pytest tests/test_urna_model.py -v`. **Verify:** row 17. **Boundaries:** model-code mapping constants live in this module with the TSE source documented.

---

### Task 18 — Composite score + clustering check
**Spec TODO:** Per-section score vector + combined percentile + clustering check — do high-scoring sections concentrate in zones/municipalities beyond chance?

**Files**
- `pipeline/src/observatory/detectors/composite.py`, `pipeline/tests/test_composite.py` (pre-authored) — create
- `pipeline/src/observatory/detectors/__init__.py` — modify: register detector
- `pipeline/src/observatory/export/shards.py` — modify: `composite` under the `ALL_r` score keys + `score_detail` threshold now driven by composite percentile ≥ 99
- `pipeline/src/observatory/export/aggregates.py` — modify: write `anomalies/clustering.json` and populate `municipalities-index.json` `flag_density`

**Work**
Composite (binding): grain is one row per `(section_id, race='ALL', round)`, rounds 1 and 2. Contributors for round r: `t1_arithmetic` (max over that section's races in round r), `t1_presence` (round r), `extreme_dominance` (PRES, r), `turnout` (PRES, r), and `split_ticket` (its round-1 score contributes to both rounds). Distribution-level detectors are excluded. Each contributor's score converts to a **mean-rank percentile** among that detector's scored sections — mean-rank pins the tie handling, and ties are massive here (tier-1 zeros). Composite = mean of available percentiles when ≥ 2 contributors exist; otherwise **no row is written** (which is why the score column can stay NOT NULL). `detail = {percentiles: {…}, n_detectors}`.
Clustering (binding): the national high set is exactly the top `ceil(0.01 · N)` sections by composite per round, ties broken deterministically by (composite desc, mun_code asc, zone asc, section asc) — a raw "percentile ≥ 99" cut does NOT select 1 % under ties. With N sections, K highs, and a zone of size n_z containing x highs, the null is **hypergeometric**: `p_z = P(X ≥ x | N, K, n_z)` (fixed-K draws without replacement; a plain Binomial(0.01) is wrong here). Benjamini–Hochberg across all zones at q = 0.05; surviving zones (and the same computation at municipality level) → `detector_summary.summary` + `anomalies/clustering.json` with context. Record the null-model choice in `detector_summary.params` and surface it on the methodology page. A permutation refinement is out of scope v1.

**Acceptance check** — `pipeline/tests/test_composite.py`:
```python
def test_composite_is_mean_of_mean_rank_percentiles(test_db, seed_multi_detector):
    seed_multi_detector(test_db)          # fixed, known score vectors across 3 detectors
    composite.Composite().run(test_db)
    row = read_scores(test_db, "composite").sort("score", descending=True).head(1)
    d = row["detail"][0]
    assert abs(row["score"][0] - sum(d["percentiles"].values()) / d["n_detectors"]) < 1e-9

def test_fewer_than_two_contributors_writes_no_row(test_db, seed_single_detector):
    seed_single_detector(test_db)
    composite.Composite().run(test_db)
    assert read_scores(test_db, "composite").height == 0

def test_planted_hot_zone_survives_bh(test_db, seed_zones):
    # 50 zones x 40 sections; highs placed DETERMINISTICALLY (no sampling): 10 in the
    # hot zone, 10 spread one-per-zone across zones 0-9
    seed_zones(test_db, zones=50, per_zone=40, hot_zone_high=10, spread_high=10)
    composite.Composite().run(test_db)
    surviving = composite.read_clusters(test_db)
    assert len(surviving) == 1 and surviving[0]["observed_high"] == 10

def test_evenly_spread_highs_yield_no_clusters(test_db, seed_zones):
    # 20 highs placed one-per-zone across 20 different zones — deterministic, no hot spot
    seed_zones(test_db, zones=50, per_zone=40, hot_zone_high=0, spread_high=20)
    composite.Composite().run(test_db)
    assert composite.read_clusters(test_db) == []
```
Run: `cd pipeline && uv run pytest tests/test_composite.py -v`. **Verify:** row 18 + re-run RR export so shards carry `composite`. **Boundaries:** runs after all per-section detectors; must tolerate a detector being absent (fewer percentiles), not crash.

---

### Task 19 — Overview page (national choropleth + headlines)
**Spec TODO:** Overview — national maps + headline findings.

**Files**
- `dashboard/src/pages/Overview/` (page, choropleth, headline cards) — create
- `dashboard/src/pages/Overview/overview.test.tsx` (pre-authored) — create
- committed BR-municipalities topojson keyed (or re-keyed via the Task 9 crosswalk) to TSE codes — create: source IBGE malhas, simplified with mapshaper, the tolerance recorded alongside so the asset is reproducible; **budget ≤ 1.5 MB gzipped**
- `docs/baselines.md` — modify: measured topojson size + Overview first-load transfer
- `dashboard/src/i18n/*.json` — modify: keys (placeholder values)

**Work**
Intent: national municipality choropleth (topojson; color by a selectable metric: leading-candidate share / turnout / `flag_density` from `municipalities-index.json` + `summary.json` — flag_density renders an "available after full analysis" empty state until Task 18's re-export populates it), headline cards from `summary.json` per-detector counts, each linking to its detector page; click municipality → drill-down route. ZZ (exterior) municipalities have no geometry and stay off the map. Map rendering approach (d3-geo vs react-simple-maps) your choice; must stay static-bundle-only (no tile servers). Taste bar applies.

**Acceptance check** — `overview.test.tsx`:
```tsx
it("renders headline cards from summary.json with links to detector pages", async () => {
  renderWithProviders(<OverviewPage />);                  // fetch mocked with fixture summary
  const cards = await screen.findAllByTestId("headline-card");
  expect(cards.length).toBeGreaterThanOrEqual(3);
  expect(cards[0].querySelector("a")).toHaveAttribute("href", expect.stringContaining("/detectors/"));
});
it("choropleth binds a data value to every index municipality that has geometry", async () => {
  renderWithProviders(<OverviewPage />);
  const map = await screen.findByTestId("choropleth");
  const bound = map.querySelectorAll("path[data-mun][data-value]").length;
  const expected = fixtureIndex.filter((m) => m.lat !== null).length;  // ZZ has no geometry
  expect(bound).toBe(expected);            // fixture-size independent by construction
});
```
Run: `cd dashboard && npm run test -- --run`. **Verify:** row 19 incl. smoke. **Boundaries:** no detector-page internals.

---

### Task 20 — Detector pages
**Spec TODO:** Detector pages (one per analysis) — methodology explainer + rankings + charts.

**Files**
- `dashboard/src/pages/Detector/` (registry-driven page: ranking table from `anomalies/{detector}.json`, per-detector chart blocks — fingerprint heatmap, digit-frequency bars, score distributions; urna-model contrast panel showing raw gap vs within-municipality gap side by side) — create
- `dashboard/src/pages/Detector/detector.test.tsx` (pre-authored) — create
- `dashboard/src/i18n/*.json` — modify: keys incl. per-detector explainer slots (placeholder values; real copy Task 22)

**Work**
One route `/detectors/:id` driven by a typed registry (id, i18n keys, which chart blocks, which data files). Ranking rows link to municipality drill-down. Digit-test pages must render the caveat slot ABOVE the results (neutral-stance requirement — structure now, copy later). Chart composition via Recharts; visual design your choice within Tailwind.

**Acceptance check** — `detector.test.tsx`:
```tsx
it("renders top-N ranking from anomalies file, ranked and linked", async () => {
  renderWithProviders(<DetectorPage id="extreme_dominance" />);
  const rows = await screen.findAllByTestId("ranking-row");
  const scores = rows.map(r => Number(r.dataset.score));
  expect(scores).toEqual([...scores].sort((a, b) => b - a));
  expect(rows[0].querySelector("a")).toHaveAttribute("href", expect.stringContaining("/municipality/"));
});
it("digit detector page renders caveat slot before results", async () => {
  renderWithProviders(<DetectorPage id="digits" />);
  const caveat = await screen.findByTestId("methodology-caveat");
  const results = screen.getByTestId("detector-results");
  expect(caveat.compareDocumentPosition(results) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
```
Run: `cd dashboard && npm run test -- --run`. **Verify:** row 20 incl. smoke on every detector route. **Boundaries:** explainer text = placeholder keys only.

---

### Task 21 — Methodology page structure
**Spec TODO:** Methodology — honest-caveats page with citations; load-bearing for credibility.

**Files**
- `dashboard/src/pages/Methodology/` — create: renders per-locale markdown (`src/i18n/methodology.pt.md`, `methodology.en.md`) with a citation list component; placeholder markdown files with the section skeleton (data sources, per-detector method, caveats incl. digit tests, reproducibility/how to re-run, citations: Klimek et al. 2012, Deckert et al. 2011, Ridout–Demétrio–Firth 1999)
- `dashboard/src/pages/Methodology/methodology.test.tsx` (pre-authored) — create

**Work**
Markdown rendering (static import, no runtime fetch of external URLs), table of contents, locale-switch swaps the markdown file. Skeleton headings are binding (Task 22 fills prose under them); citation entries carry structured `{authors, year, title, venue}`.

**Acceptance check** — `methodology.test.tsx`:
```tsx
it("renders all skeleton sections and the citation list in both locales", async () => {
  for (const loc of ["pt-BR", "en"] as const) {
    renderWithProviders(<MethodologyPage />, { locale: loc });
    for (const id of ["sources", "detectors", "caveats", "reproduce", "citations"])
      expect(await screen.findByTestId(`section-${id}`)).toBeInTheDocument();
    expect(screen.getAllByTestId("citation").length).toBeGreaterThanOrEqual(3);
    cleanup();
  }
});
```
Run: `cd dashboard && npm run test -- --run`. **Verify:** row 21 incl. smoke. **Boundaries:** structure and placeholders only — no final prose (that is Task 22's, and it is sol's).

---

### Task 22 — Bilingual public copy (pt-BR + EN) — **codex-implementation / gpt-5.6-sol**
**Spec TODO:** Bilingual pt-BR (default) / English. All public copy authored by gpt-5.6-sol (codex CLI leg) during implementation.

**Files**
- `dashboard/src/i18n/pt-BR.json`, `en.json` — modify: replace every placeholder value
- `dashboard/src/i18n/methodology.pt.md`, `methodology.en.md` — modify: full prose under the Task 21 skeleton
- `dashboard/src/i18n/copy-complete.test.ts` (pre-authored) — create

**Work**
**Route: `codex-implementation` skill (gpt-5.6-sol) via `~/.claude/bin/codex-run.sh` — not a Claude agent.** Brief to sol must include: the neutral forensic stance verbatim from the spec ("never fraud-finder, never debunking-only; every flag carries its local baseline, sample size, and score/p-value"), the digit-test caveats requirement (Deckert et al. 2011 discussed explicitly), per-detector method summaries sourced from this plan's Tasks 6–18 Work sections, and the full key list extracted from the placeholder files. pt-BR is the primary authored language; EN is a faithful parallel, not a machine-flavored translation. Claude's role in this slice: assemble the brief, run codex, then REVIEW the diff for stance violations and key completeness — not author copy.

**Acceptance check** — `copy-complete.test.ts`:
```ts
it("no placeholder values remain and locales are key-complete", () => {
  const pt = flatten(ptBR), en = flatten(enUS);
  expect(Object.keys(pt).sort()).toEqual(Object.keys(en).sort());
  for (const [k, v] of Object.entries({ ...pt, ...en }))
    expect(v, k).not.toMatch(/TODO|PLACEHOLDER|lorem/i);
});
it("methodology prose covers every skeleton section in both files", () => {
  for (const md of [methodologyPt, methodologyEn])
    for (const h of ["## ", "### "])                     // each skeleton heading followed by prose
      expect(md).not.toMatch(new RegExp(`${h}.+\\n+\\s*(##|$)`));
});
```
Run: `cd dashboard && npm run test -- --run`. **Verify:** row 22 — plus smoke in BOTH locales. Proof-of-run: codex registry line + report per Codex ops rules. **Boundaries:** sol edits ONLY the four i18n content files; component code untouched.

---

### Task 23 — All-Brazil scale-out + national baselines
**Spec TODO:** ~100 M rows, all races, both rounds; 5,570 municipality shards; validation gates on real national data.

**Files**
- `docs/baselines.md` — modify: every budget-table measurement, national
- `pipeline/src/observatory/**` — modify only as needed for throughput/memory fixes surfaced by the national run (report every such change)
- `Makefile` — modify: `pipeline-all` finalized

**Work**
Pre-flight (binding): BEFORE downloading, compare free disk against the Task 5-extrapolated storage baseline in `docs/baselines.md` plus 20 % headroom; abort with a clear message if short (measured 2026-08-04: ~29 GiB free — tight). Zips stream (never extracted) and staging tables are UNLOGGED/dropped, per Tasks 2/4. Then run the full pipeline: download all UFs, ingest (~100 M fact rows), analyze all detectors, export all ~5,570 shards, both gates green. Expected pain points (pre-authorized to fix, report each): ingest memory/batching, detector vectorization at national scale, São Paulo capital shard splitting. Record in `docs/baselines.md`: national quarantine rate, fact_votes count, per-stage wall-clock, shard-size distribution (p50/p95/max, before/after split), file count, total bytes, largest file. **Budget breaches are plan defects — stop and escalate; do not adjust budgets.**

**Acceptance check** — the existing integration suite at national scope (no new test file — the gates ARE the acceptance):
Run: `make pipeline-all` (repo root), then `cd pipeline && uv run pytest -m integration -v` — reconciliation gate and export gate green against the national database, plus the full non-integration suite still green.

**Verify:** table row 23. **Boundaries:** no new features; no detector-logic changes (a detector that breaks at scale for statistical reasons — not perf — is a `blocked:` return).

---

### Task 24 — Cloudflare Pages deploy config + production smoke
**Spec TODO:** Zero cost to publish; free-tier hosting (Cloudflare Pages primary target). Immutable + aggressive CDN caching.

**Files**
- `dashboard/public/_headers` — create: `data-manifest.json` → `no-store`; every hashed file under `/data/` → `Cache-Control: public, max-age=31536000, immutable`
- `dashboard/scripts/check-limits.mjs` — create: counts files and max file size over the EXACT deploy directory (`dist/` incl. data); fails above 20,000 files or 25 MB/file; result appended to `docs/baselines.md`
- `docs/deploy.md` — create: exact wrangler/Pages steps, env-free build, re-deploy procedure (new export = new hashed files + new manifest, no stale mixing by construction)
- `dashboard/vite.config.ts` — modify if needed for asset handling of `.json.gz`

**Work**
Intent: `npm run build` output + `public/data/` deploys as-is to Pages within limits (Task 23 baselines prove counts). Verify `.json.gz` files are served as opaque bytes (no double-decompression against the Task 11 client-side DecompressionStream) — if Pages applies Content-Encoding to `.gz` paths, adjust serving strategy (e.g. drop the `.gz` suffix, keep gzipped payload) and report the deviation. No secrets, no build-time env.

**Acceptance check:** `npm run build && node scripts/check-limits.mjs dist && npm run smoke` with real national data linked into `public/data/` — build within hosting limits, loads, renders, zero console errors, one municipality drill-down fetch succeeds. Then a **codex-computer-use** pass against the deployed preview URL: load overview, switch locale, open a drill-down, screenshot evidence. Real-site verification is risk-gated and this is the user-visible release surface — required here.

**Verify:** table row 24. **Boundaries:** no CI/CD automation (out of scope v1); the final production deploy action is the user's.

---

## Self-review record

Fix pass (2026-08-04, post plan-review wave — the one consolidated pass): applied all 11 blockers, 13 should-fixes, and 8 notes. Material frozen-contract changes downstream slices consume: (1) reconciliation respecified as three category-scoped comparisons over two NEW rollup sources (`votacao_partido_munzona`, `detalhe_votacao_munzona`); (2) `votavel_kind` introduced end-to-end (parse → `fact_votes` → shards → gate); (3) shard schema v1 revised — race ids always round-suffixed, `stats.legenda`/`null_votes`, `scores`/`score_detail` keyed by race id ∪ `ALL_1`/`ALL_2` — the binding contract is the REVISED Task 8 block; (4) `Detector.run` returns `DetectorResult` and state payloads persist via `analysis.state_payloads` (Task 9's hook mechanism removed); (5) clustering null is hypergeometric over a deterministic top-1 % set; (6) storage, topojson, and deploy-directory budgets added with measurement points; (7) `dim_candidate` uniqueness now includes `uf`.

Spec coverage: every spec section maps to ≥ 1 task — ingestion rules → 2–5; detectors 1–8 → 6/6/7/13/14/15/16/17; composite+clustering → 18; export A/B + sharding care 1–5 → 8/9/10; dashboard pages → 12/19/20/21; bilingual copy → 11 (scaffold) + 22 (authoring); testing section → the acceptance tests themselves; hosting constraints → 10/23/24. Type consistency: `analysis.section_scores` DDL (Task 6) is the single scoring contract all detectors write; shard schema (Task 8) is the single contract Tasks 10/11/12/18 read; manifest shape (Task 10) is what Task 11's `resolve()` consumes. No placeholders: every task carries runnable acceptance assertions; implementer-authored test *fixtures* (seed helpers) are deliberate rung-appropriate freedom, with assertions fixed upstream.
