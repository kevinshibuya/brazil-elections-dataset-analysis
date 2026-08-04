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

## File structure

```
pipeline/                     # uv project, package "observatory"
  pyproject.toml
  01_download.py … 04_export.py   # thin numbered wrappers over the package
  src/observatory/
    config.py                 # DATABASE_URL, paths, UF filter, constants
    sources.py                # TSE dataset URL manifest + checksums
    download.py
    parse.py                  # ALL TSE quirks live here (latin-1, ';', #NULO#, -1)
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

`PGURL` = `postgresql://postgres:postgres@localhost:5432/elections` (docker compose up first). Pipeline commands run from `pipeline/`, dashboard commands from `dashboard/`. Integration tests are deselected by default (`-m "not integration"` in pytest addopts) and run explicitly where listed.

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
| 24 | `npm run build` · `npm run smoke` against `vite preview` of the production build · codex-computer-use pass on the deployed preview URL |

Browser truth: every dashboard slice (11, 12, 19, 20, 21, 22, 24) verifies with the headless smoke in addition to typecheck/vitest — static checks are blind to runtime module-resolution and data-loading failures.

**Execution notes.** Execute on a `feat/election-anomaly-observatory` branch off main (never commit to main). Each task ends with a commit. Before Task 1: the single three-leg plan-review wave, then one consolidated fix pass. Detector slices 13–18 are mutually independent (parallelizable after 6); dashboard slices 19–21 are independent of detectors 13–18 except where their data files are inputs — run detector slice before its page slice, or point the page at fixture data.

---

### Task 1 — Repo scaffold + tooling
**Spec TODO:** Components: `pipeline/` (Python, uv-managed, numbered scripts), `dashboard/` (Vite + React + TS + Tailwind), `data/` (`raw/` git-ignored, `fixtures/` committed), `docs/`.

**Files**
- `pipeline/pyproject.toml`, `pipeline/src/observatory/__init__.py`, `pipeline/src/observatory/config.py`, `pipeline/tests/test_scaffold.py` — create
- `dashboard/` — create: Vite React-TS app with Tailwind, Vitest, Playwright dev-dep, `scripts/smoke.mjs`, `src/App.tsx` placeholder shell
- `docker-compose.yml`, `Makefile`, `.gitignore`, `docs/baselines.md` (empty ledger with table header) — create

**Work**
Intent: a contributor with Docker + uv + node runs `docker compose up -d`, `make test-pipeline`, `make test-dashboard`, `make smoke` green in one sitting. Binding constraints: Python 3.12, uv-managed; pytest `addopts = -m "not integration"` with `integration` and `db` markers registered; `config.py` reads `DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/elections`) and `DATA_DIR`; `.gitignore` covers `data/raw/`, `dashboard/public/data/`, `node_modules`, `.venv`; Tailwind + strict TS (`"strict": true`, no `any`); `smoke.mjs` builds nothing — it launches Playwright chromium against a running `vite preview` (the Makefile target builds then previews), asserts `#root` has rendered children and **zero console errors**, exits non-zero otherwise. Makefile targets: `pipeline UF=…`, `pipeline-all`, `test-pipeline`, `test-dashboard`, `smoke` (later tasks fill pipeline targets; stub them now with a clear "not implemented" failure). Internal structure otherwise your choice; report deviations.

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

**Interfaces (produced):** `sources.datasets(uf: str | None) -> list[Dataset]` where `Dataset(name, url, kind, uf)` and kinds are `votes_section`, `detail_section`, `rollup_munzona`, `urna_correspondence`. `download.fetch_all(uf: str | None) -> None` idempotent into `data/raw/`.

**Work**
Intent: one command fetches exactly the needed TSE zips, verifies/records SHA-256 in `data/raw/checksums.json`, never re-downloads a file whose checksum matches, never mutates existing raw files. `--uf RR` narrows to state files + national president file + national rollup. Reference URL patterns (verify against the live portal; fix in `sources.py` if drifted — report the drift):
`https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/votacao_secao_2022_{UF}.zip`, `…/detalhe_votacao_secao/detalhe_votacao_secao_2022_{UF}.zip`, `…/votacao_candidato_munzona/votacao_candidato_munzona_2022.zip`, and the 2022 correspondência-esperada (urna model) files under `…/eleicoes/eleicoes2022/`. Tests never hit the network: fake fetcher injected. HTTP plumbing your choice.

**Acceptance check** — `pipeline/tests/test_download.py`:
```python
from observatory import sources

def test_rr_subset_includes_national_president_and_rollup():
    ds = sources.datasets("RR")
    kinds = {d.kind for d in ds}
    assert {"votes_section", "detail_section", "rollup_munzona"} <= kinds
    assert any(d.uf == "RR" for d in ds if d.kind == "votes_section")
    assert all(d.url.startswith("https://cdn.tse.jus.br/") for d in ds)

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

**Verify:** table row 2. **Boundaries:** no parsing, no DB. If the correspondence-file URL scheme can't be confirmed from the portal, download the rest and return `blocked:` naming exactly that dataset (Task 17 depends on it).

---

### Task 3 — TSE parser + quirk normalization + committed real excerpts
**Spec TODO:** TSE quirks (latin-1, semicolon-separated, `#NULO#`, `-1` sentinels) normalized at ingestion, documented in one module.

**Files**
- `pipeline/src/observatory/parse.py`, `pipeline/tests/test_parse.py` (pre-authored) — create
- `data/fixtures/tse/` — create: real-file excerpts (see Work)
- `pipeline/tests/conftest.py` — create: fixture-path helpers

**Interfaces (produced):** `parse.read_votes(path) -> pl.DataFrame` with columns `uf, mun_code, mun_name, zone, section, race, round, cand_number, cand_name, party, votes` and `parse.read_detail(path) -> pl.DataFrame` with `uf, mun_code, zone, section, race, round, aptos, comparecimento, abstentions, blank, null`. `race` ∈ {`PRES`,`GOV`,`SEN`,`DEPFED`,`DEPEST`,`DEPDIS`} mapped from CD_CARGO (1,3,5,6,7,8); `round` ∈ {1,2}. Rows that fail normalization are returned separately: each reader returns `(good: pl.DataFrame, bad: pl.DataFrame)` where `bad` has `raw_line, reason`.

**Work**
First download RR for real (`uv run python 01_download.py --uf RR`), then cut committed excerpts: for each file kind, the header line + ~200 representative rows + hand-picked rows exhibiting each quirk (latin-1 accents, `#NULO#`/`#NULO`, `-1` and `-3` sentinels, quoted semicolons if present). Excerpts must remain byte-identical to source lines (latin-1, not re-encoded) — they are the regression net for "they loaded the data wrong". Document every quirk you actually observe as a comment block at the top of `parse.py`; that module is the single home for quirk knowledge. Column-name constants from TSE headers (`SG_UF`, `CD_MUNICIPIO`, `NR_ZONA`, `NR_SECAO`, `CD_CARGO`, `NR_TURNO`, `NR_VOTAVEL`/`NR_CANDIDATO`, `QT_VOTOS`, …) — verify against the real header, don't trust this list blindly. Parsing engine (polars `read_csv` with encoding vs manual) is your choice.

**Acceptance check** — `pipeline/tests/test_parse.py` (representative core; the committed excerpts make these exact):
```python
from observatory import parse
from tests.conftest import fixture

def test_votes_excerpt_parses_clean():
    good, bad = parse.read_votes(fixture("tse/votacao_secao_2022_RR_excerpt.csv"))
    assert bad.height == 0
    assert set(good["race"].unique()) <= {"PRES","GOV","SEN","DEPFED","DEPEST","DEPDIS"}
    assert good["votes"].min() >= 0                      # sentinels normalized, not leaked
    assert good.filter(good["cand_name"] == "#NULO#").height == 0

def test_detail_excerpt_parses_clean():
    good, bad = parse.read_detail(fixture("tse/detalhe_votacao_secao_2022_RR_excerpt.csv"))
    assert bad.height == 0
    assert (good["aptos"] >= 0).all() and (good["comparecimento"] >= 0).all()

def test_malformed_row_goes_to_bad_not_dropped():
    good, bad = parse.read_votes(fixture("tse/votacao_malformed_excerpt.csv"))
    assert bad.height >= 1 and "reason" in bad.columns
```
Run: `cd pipeline && uv run pytest tests/test_parse.py -v`.

**Verify:** table row 3. **Boundaries:** no DB writes, no rollup-file parsing beyond what `read_votes`-style readers need (rollup reader included here — same module, same contract shape: `parse.read_rollup(path)`). Excerpt curation is judgment — that's why this is integrator-rung.

---

### Task 4 — Postgres schema + atomic ingest + quarantine gate
**Spec TODO:** Loads are atomic: staging → validate → swap. No silent row-dropping: unparseable rows land in a `quarantine` table with a reason; > 0.01 % quarantined = hard failure.

**Files**
- `pipeline/src/observatory/db.py`, `pipeline/src/observatory/ingest.py`, `pipeline/02_ingest.py`, `pipeline/tests/test_ingest.py` (pre-authored) — create
- `Makefile` — modify: ingest step

**Interfaces (produced):** Tables exactly as specced: `dim_municipality(code PK, name, uf, region, ibge_code NULL)`, `dim_section(id PK, uf, mun_code FK, zone, section, aptos_r1, aptos_r2, UNIQUE(uf,mun_code,zone,section))`, `dim_candidate(id PK, race, round, number, name, party, coalition NULL)`, `fact_votes(section_id, race, round, candidate_id, votes)` partitioned by race, `fact_section_stats(section_id, race, round, aptos, comparecimento, abstentions, blank, null_votes)`, `quarantine(source_file, raw_line, reason, at)`, `rollup_munzona(...)` mirroring TSE's official totals file, and `meta.gate_runs(gate, ok, detail, at)`. `ingest.run(uf: str | None) -> IngestReport(rows_loaded, rows_quarantined, quarantine_rate)`; raises `QuarantineGateError` when rate > 0.0001.

**Work**
Intent: `02_ingest.py --uf RR` parses every raw file through Task 3's readers into `staging.*` tables, validates (FK integrity, quarantine rate), then swaps into place in one transaction (rename or partition attach — your choice; the observable contract is that a failed run leaves the previous good state untouched). `bad` rows from parsers → `quarantine` with reason. The 0.01 % gate compares quarantined vs total attempted rows across the run. Re-running from scratch (`make pipeline UF=RR`) must be deterministic. Indexing/copy strategy (COPY vs polars write) your choice — 100 M rows nationally, so COPY-based is strongly suggested; report your throughput on RR.

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
    report = ingest.load_batch(test_db, fixture_batch_one_bad)
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
- `docs/baselines.md` — modify: record RR quarantine rate, row counts, ingest wall-clock

**Work**
Gate contract (load-bearing, binding): group `fact_votes` by `(mun_code, race, round, candidate_number)` and compare with the same grouping of `rollup_munzona` (TSE munzona file aggregated over zones); compare `fact_section_stats` sums per `(mun_code, race, round)` against rollup turnout columns. Tolerance zero. Every mismatch row → `reconciliation_report(mun_code, race, round, key, ours, theirs, diff)`; any mismatch → `ReconciliationError` after writing the full report (never fail on first diff — the report is the diagnostic). Success and failure both stamp `meta.gate_runs`. This gate doubles as the e2e integration test: the acceptance run is the real RR pipeline.

**Acceptance check** — `pipeline/tests/test_reconcile.py`:
```python
import pytest
from observatory import reconcile

def test_mismatch_writes_full_report_then_raises(test_db, seeded_mismatch):
    with pytest.raises(reconcile.ReconciliationError):
        reconcile.run_gate(test_db)
    rep = reconcile.read_report(test_db)
    assert rep.height == seeded_mismatch.expected_diffs   # ALL diffs, not just first
    assert {"ours", "theirs", "diff"} <= set(rep.columns)

@pytest.mark.integration
def test_rr_real_data_reconciles(real_db):
    assert reconcile.run_gate(real_db).ok    # zero mismatches on real RR ingest
```
Run: `cd pipeline && make pipeline UF=RR && uv run pytest tests/test_reconcile.py -v -m integration` (plus the unit test un-marked).

**Verify:** table row 5. Also: record baselines (budget table) in `docs/baselines.md`. **Boundaries:** no detector work. If real RR data does NOT reconcile after honest investigation of our side, return `blocked:` with the report excerpt — a genuine TSE-side discrepancy is a plan-level finding, not something to paper over with tolerance.

---

### Task 6 — Detector framework + Tier 1 integrity checks
**Spec TODO:** Tier 1 — arithmetic consistency; cross-race presence. Each detector writes per-section scores plus a national summary to `analysis.*`.

**Files**
- `pipeline/src/observatory/detectors/base.py`, `detectors/tier1.py`, `pipeline/03_analyze.py`, `pipeline/tests/test_tier1.py` (pre-authored) — create
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
```
Score convention: **higher = more anomalous**; probabilistic detectors store `score = -log10(max(p, 1e-16))`; `detail` carries the human context (baseline, n, tails). Python side: `class Detector(Protocol): name: str; def run(self, db) -> None` + registry `detectors.all()`; `03_analyze.py --only <name>` runs a subset.

**Work**
Tier 1 checks, exactly: (a) per (section, race, round): nominal + blank + null == comparecimento; comparecimento ≤ aptos; no negative counts — violations get score 1.0 per violated rule (detail lists rules), clean sections score 0; (b) cross-race presence: a section reporting any race must report every race valid for its UF and round (DEPDIS only in DF, GOV/SEN per schedule, PRES round 2 everywhere that had round 2) — missing races scored per missing race with detail. Framework internals (how detectors get polars frames, batching) your choice.

**Acceptance check** — `pipeline/tests/test_tier1.py` (synthetic frames built inline via a `seed_sections(...)` helper the implementer writes in conftest; assertions fixed):
```python
import pytest
from observatory.detectors import tier1

pytestmark = pytest.mark.db

def test_arithmetic_violation_flagged(test_db, seed_sections):
    seed_sections(test_db, [dict(comp=100, blank=5, null=5, nominal=95, aptos=120)])  # 105≠100
    tier1.Arithmetic().run(test_db)
    scores = tier1.read_scores(test_db, "t1_arithmetic")
    assert scores.height == 1 and scores["score"][0] >= 1.0

def test_clean_section_scores_zero(test_db, seed_sections):
    seed_sections(test_db, [dict(comp=100, blank=5, null=5, nominal=90, aptos=120)])
    tier1.Arithmetic().run(test_db)
    assert tier1.read_scores(test_db, "t1_arithmetic")["score"].max() == 0.0

def test_missing_race_flagged(test_db, seed_sections):
    seed_sections(test_db, [dict(races=["PRES"])])   # UF where GOV/DEPFED expected
    tier1.CrossRacePresence().run(test_db)
    assert tier1.read_scores(test_db, "t1_presence")["score"].max() >= 1.0
```
Run: `cd pipeline && uv run pytest tests/test_tier1.py -v`.

**Verify:** table row 6. **Boundaries:** no Tier 2/3 logic; no export awareness.

---

### Task 7 — Extreme-dominance detector (beta-binomial)
**Spec TODO:** Extreme-dominance sections ("300×0" done right) — probability of a result at least this extreme given the municipality's vote share, via beta-binomial.

**Files**
- `pipeline/src/observatory/detectors/extreme_dominance.py`, `pipeline/tests/test_extreme_dominance.py` (pre-authored) — create

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
Score = `-log10(max(min(p_hi, p_lo), 1e-16))`; sections with `n_i = 0` are not scored; per-section `detail` = `{cand, k, n, p_hat, rho_hat, p_hi, p_lo, baseline}`. Section-level score for the composite = max over scored candidates. Summary: national count with min-tail < 1e-4, and the top of the ranked list.

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
(`seed_votes` fixture is the implementer's; seeded RNG for determinism.) Run: `cd pipeline && uv run pytest tests/test_extreme_dominance.py -v`.

**Verify:** table row 7. **Boundaries:** presidential only; no export changes. If the leave-one-out sufficient-statistic algebra doesn't hold together when implemented (it should — subtraction of `k_i, n_i, n_i²` from the sums), return `blocked:` rather than switching silently to a full-baseline fit.

---

### Task 8 — Shard encoder + per-zone auto-split (RR export)
**Spec TODO:** Compact columnar encoding; size budget enforced by exporter; oversized municipalities auto-split into per-zone subshards behind an index.

**Files**
- `pipeline/src/observatory/export/shards.py`, `pipeline/04_export.py`, `pipeline/tests/test_shards.py` (pre-authored) — create
- `data/fixtures/golden_shard/` — create: one Python-encoded RR shard committed as golden fixture for the TS decoder (Task 11)
- `docs/baselines.md` — modify: RR shard size distribution
- `Makefile` — modify: export step

**Work**
The shard schema is load-bearing and shipped verbatim (v = 1). Encoder internals your choice; output `dashboard/public/data/shards/{mun_code}.json.gz`.

```jsonc
// Regular shard — shards/{code}.json.gz
{
  "v": 1, "code": "03018", "name": "Boa Vista", "uf": "RR",
  "races": ["PRES_1", "PRES_2", "GOV_1", "SEN", "DEPFED", "DEPEST"],   // "{race}_{round}" ids
  "cand": { "PRES_1": [[13, "LULA", "PT"], [22, "JAIR BOLSONARO", "PL"]] },  // per-race lookup
  "zone":    [8, 8, 9],          // parallel arrays, one entry per section,
  "section": [1, 2, 1],          // sorted by (zone, section); index = section position
  "stats": { "PRES_1": { "aptos": [], "comp": [], "blank": [], "null": [] } },  // dense, aligned
  "votes": { "PRES_1": { "sec": [0,0,1], "cand": [13,22,13], "n": [201,102,188] } },
        // sparse COO triplets, sorted by (sec, cand), zero-vote pairs omitted,
        // cand = candidate number resolved via "cand" lookup
  "scores": { "extreme_dominance": [2.1, 0.3, null] },   // per-detector dense, null = unscored
  "score_detail": { "0": { "extreme_dominance": { /* detail JSON from analysis */ } } }
        // ONLY for sections whose composite/any score >= the flag threshold (keeps shards small)
}
// Split index — written AT shards/{code}.json.gz when the regular encoding exceeds budget:
{ "v": 1, "split": true, "code": "35157", "name": "São Paulo", "uf": "SP", "zones": [1, 2, 3] }
// …with per-zone subshards at shards/{code}-z{zone}.json.gz using the regular schema
// restricted to that zone (plus "zone_only": <zone>).
```
Budget enforcement lives here: after encoding, if gzipped size > 300 KB → re-encode as split; if any subshard still > 1 MB → raise (hard fail per spec). Encoder must be a pure function `encode_shard(mun_data) -> dict` separable from file I/O (the golden fixture and round-trip tests need it).

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

def test_oversized_municipality_splits_by_zone(big_shard_input):   # synthetic >300KB gz
    out = shards.write_shard(big_shard_input, tmp=True)
    idx = json.loads(gzip.decompress(out.index_bytes))
    assert idx["split"] is True and len(idx["zones"]) >= 2

def test_subshard_over_1mb_hard_fails(giant_zone_input):
    with pytest.raises(shards.ShardBudgetError):
        shards.write_shard(giant_zone_input, tmp=True)
```
Run: `cd pipeline && uv run pytest tests/test_shards.py -v`, then real run: `uv run python 04_export.py --uf RR --shards-only` and record sizes in `docs/baselines.md`.

**Verify:** table row 8. **Boundaries:** aggregates files are Task 9; validation gate + manifest are Task 10. Schema field names are frozen — the TS decoder (Task 11) is written against them.

---

### Task 9 — Aggregates export
**Spec TODO:** Aggregates (eager-loaded): `summary.json`; `states/{uf}.json`; `anomalies/{detector}.json` (top-500 ranked with context); `municipalities-index.json`.

**Files**
- `pipeline/src/observatory/export/aggregates.py`, `pipeline/tests/test_aggregates.py` (pre-authored) — create
- `pipeline/04_export.py` — modify: wire aggregates

**Work**
All files carry `"v": 1`. Contents (binding): `summary.json` — national totals per race/round, turnout, per-detector headline counts from `detector_summary`; `states/{uf}.json` — state totals per race, turnout, plus per-detector state payloads via a hook: `aggregates.py` exposes `register_state_payload(detector, fn)` so later detector slices (15, 16) attach fingerprint grids / digit results without rewriting this module; `anomalies/{detector}.json` — national top-500 by score with context `{mun_code, mun_name, uf, zone, section, race, round, score, detail}`; `municipalities-index.json` — `[code, name, uf, lat, lon, headline_shares]` arrays (columnar). Centroids: use a committed static municipality-centroid dataset (IBGE-derived, checked into `data/fixtures/`; source noted in the file). Exact intra-file layout beyond this is your choice.

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

**Verify:** table row 9. **Boundaries:** no manifest/hashing (Task 10); detector-specific state payloads land in Tasks 15/16 via the hook.

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
{ "v": 1, "schema": 1, "generated": "2026-08-04T12:00:00Z",
  "files": { "shards/03018.json.gz": { "path": "shards/03018.ab12cd34.json.gz",
                                        "sha256": "…", "bytes": 48213 },
             "summary.json": { "path": "summary.ab99ffee.json", "sha256": "…", "bytes": 3120 } },
  "count": 22, "bytes": 1048576 }
```
Physical files are written under their hashed names (first 8 hex of sha256 before the extension); logical names appear only in the manifest. Gate (binding, in order, ALL files re-read from disk): (1) parses; (2) `v == 1`; (3) shard totals — per race, COO sums — equal Postgres `fact_votes` sums for that municipality, and stats arrays sum to `fact_section_stats`; (4) every shard ≤ 1 MB, warn-listing those > 300 KB; (5) `count ≤ 20_000`, every file ≤ 25 MB. Any failure → `ExportGateError` after a full failure report (all findings, not first). Gate pass stamps `meta.gate_runs`.

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
Run: `cd pipeline && uv run pytest tests/test_export_gate.py -v`, then `make pipeline UF=RR` must finish with the gate green.

**Verify:** table row 10. **Boundaries:** no dashboard code. The gate re-reads from disk — never validates in-memory objects it was handed.

---

### Task 11 — Dashboard shell: data layer, shard decoder, i18n scaffold
**Spec TODO:** Dashboard — React Query as fetch/cache layer for shards. Schema versioning — dashboard refuses mismatches. Bilingual: pt-BR default, EN toggle.

**Files**
- `dashboard/src/data/manifest.ts`, `src/data/shards.ts` (decoder), `src/data/hooks.ts`, `src/data/types.ts` — create
- `dashboard/src/i18n/` (`index.tsx`, `pt-BR.json`, `en.json` with placeholder keys), `src/App.tsx`, router + layout, locale toggle — create/modify
- `dashboard/src/data/shards.test.ts`, `src/i18n/i18n.test.ts` (pre-authored) — create
- `dashboard/public/data/` ← RR export output present locally (not committed)

**Interfaces (produced):** `loadManifest(): Promise<Manifest>` (fetched with `cache: "no-store"`); `resolve(logical: string): string` via manifest; `decodeShard(bytes: ArrayBuffer): Shard` — gunzips via `DecompressionStream("gzip")` (decided: hosting serves `.json.gz` as opaque bytes; the client decompresses — do not rely on CDN Content-Encoding) and parses into a typed `Shard` matching Task 8's schema exactly, resolving split indexes transparently (`useShard(munCode)` returns merged sections whether or not the municipality is split); throws `SchemaVersionError` when `v !== 1`, and the app surfaces a bilingual "please refresh" state. i18n: `t(key)` over flat JSON per locale, pt-BR default, persisted toggle; ALL user-visible strings go through `t()` from day one — English/Portuguese placeholder values are fine until Task 22, hardcoded JSX strings are not.

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
Intent: search a municipality (via `municipalities-index.json`), lazy-load its shard through `useShard`, render a virtualized/paginated section table (zone, section, per-race shares, per-detector score badges), filter by zone / race / min-score, click a section → anomaly panel showing each detector's score with its `score_detail` context (baseline share, n, tails) — the neutral-stance surface: every flag with its local baseline and sample size. Split municipalities must be indistinguishable to the user. UI composition and styling your choice (taste bar applies); all strings via `t()`.

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
- `pipeline/src/observatory/coalitions.py` — create: the 2022 presidential coalition → party lists as reviewed constants (round 1, both leading coalitions), with TSE source noted in comments

**Work**
Per section, per presidential candidate c (top-2, round 1): `d_i = share_pres(c, i) − share_dep(coalition_parties(c), i)` where the deputy share is the summed federal-deputy vote share of c's coalition parties in that section. The reference distribution is the **municipality's own** `{d_j}` (this is what un-flags Maranhão-style alliances — the whole municipality shares the pattern, so no section deviates from it): robust z `= (d_i − median_m(d)) / (1.4826 · MAD_m(d))`, leave-one-out not required (medians are resistant), municipalities with < 10 sections fall back to state distribution with `detail.baseline = "state"`. Score `= |z| / 2` so z = 6 → score 3 (comparable scale to −log10 p detectors; noted in detector_summary.params). Detail: `{cand, d, median, mad, z, baseline}`.

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

**Work**
Fully specified: per (race=PRES, round) — turnout `t_i = comparecimento / aptos`; robust z against the municipality's sections `z = (t_i − median) / (1.4826 · MAD)`; both signs flagged; score `= |z| / 2`; `MAD == 0` → fall back to state distribution (`detail.baseline = "state"`); `< 10` sections → same fallback. Detail `{t, median, mad, z, baseline}`.

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
- `pipeline/src/observatory/export/aggregates.py` — modify: register state payload via the Task 9 hook

**Work**
Fully specified: per (state, presidential round, leading candidate = national round winner): 2D histogram over sections, x = turnout %, y = candidate share of valid votes %, 100×100 grid of 1 % bins, raw counts (client renders log-scaled). Corner mass metric: fraction of sections with turnout > 90 % AND share > 90 %, reported in `detector_summary.summary` per state. This detector produces the state payload `{grid, corner_mass, n_sections}` through `register_state_payload("fingerprint", …)`; per-section scores are NOT written (distribution-level detector — `sections_scored = 0`; composite ignores it).

**Acceptance check** — `pipeline/tests/test_fingerprint.py`:
```python
def test_grid_shape_and_mass_conservation(test_db, seed_state):
    seed_state(test_db, sections=500)
    fp = fingerprint.Fingerprint().run(test_db)
    grid = fp.state_payload("RR")["grid"]
    assert len(grid) == 100 and len(grid[0]) == 100
    assert sum(map(sum, grid)) == 500

def test_planted_corner_smear_raises_corner_mass(test_db, seed_state):
    seed_state(test_db, sections=500, corner_planted=50)   # turnout>95%, share>95%
    fp = fingerprint.Fingerprint().run(test_db)
    assert fp.state_payload("RR")["corner_mass"] >= 50 / 550
```
Run: `cd pipeline && uv run pytest tests/test_fingerprint.py -v`. **Verify:** row 15. **Boundaries:** no rendering (Task 20 draws it).

---

### Task 16 — Digit tests (second-digit Benford + last-digit uniformity)
**Spec TODO:** Digit tests per state and municipality-size stratum. Presented as screening only, Deckert et al. caveats explicit.

**Files**
- `pipeline/src/observatory/detectors/digits.py`, `pipeline/tests/test_digits.py` (pre-authored) — create
- `pipeline/src/observatory/export/aggregates.py` — modify: register state payload

**Work**
Fully specified: over section-level candidate vote counts (presidential, both rounds, top-2 candidates): (a) second-digit test on counts ≥ 10 — chi-square against Benford's second-digit distribution; (b) last-digit test on counts ≥ 100 — chi-square against uniform(0–9). Strata: per state × municipality-size bucket (`small < 20` sections, `mid 20–99`, `large ≥ 100`). Output per stratum: `{test, n, chi2, p, digit_freqs, expected_freqs}` via the state-payload hook + national summary. No per-section scores (screening-level; `sections_scored = 0`). The Deckert/Mebane caveat text itself is Task 22 copy — this slice just exports the numbers.

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

def test_minimum_count_filters_applied(test_db, seed_counts_small):
    digits.Digits().run(test_db)                       # all counts < 10
    assert all(s["n"] == 0 for s in digits.read_strata(test_db))
```
Run: `cd pipeline && uv run pytest tests/test_digits.py -v`. **Verify:** row 16. **Boundaries:** numbers only; no caveat prose.

---

### Task 17 — Urna-model comparison
**Spec TODO:** Compare pre-2020 vs 2020 urna models *within* municipalities. Urna model per section comes from TSE correspondence files.

**Files**
- `pipeline/src/observatory/detectors/urna_model.py`, `pipeline/tests/test_urna_model.py` (pre-authored) — create
- `pipeline/src/observatory/parse.py`, `sources.py`, `ingest.py` — modify: correspondence-file reader; `dim_section.urna_model` column (`pre2020` | `ue2020` | NULL) populated at ingest
- `pipeline/src/observatory/export/aggregates.py` — modify: national + per-state payload

**Work**
Intent: replicate the 2022 controversy's claim properly. For each municipality containing BOTH model classes (map TSE model codes: UE2009/2010/2011/2013/2015 → `pre2020`, UE2020 → `ue2020`): within-municipality difference in Bolsonaro share (PRES round 2), Δ_m = share(pre2020 sections) − share(ue2020 sections), vote-weighted. National result: vote-weighted mean of Δ_m with a cluster-robust (municipality-level) 95 % CI, presented ALONGSIDE the raw national gap (the geography artifact) — both numbers exported so the dashboard can show the contrast. Per-section scores: none (`sections_scored = 0`). Correspondence-file quirks live in `parse.py` like all others. If real correspondence files lack per-section model data for both rounds, return `blocked:` with what they DO contain.

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
- `pipeline/src/observatory/export/shards.py` — modify: `scores.composite` + `score_detail` threshold now driven by composite percentile ≥ 99

**Work**
Composite (binding): for each per-section detector (tier1 ×2, extreme_dominance, split_ticket, turnout — distribution-level detectors excluded), convert scores to national percentile ranks among scored sections; composite = mean of available percentiles (≥ 2 detectors required, else NULL); store as detector `composite` with `detail = {percentiles: {…}, n_detectors}`. Clustering (binding): mark sections with composite percentile ≥ 99 as "high"; per zone, `p = P(Binomial(n_zone, 0.01) ≥ observed_high)`; Benjamini–Hochberg across all zones at q = 0.05; surviving zones (and the same at municipality level) go to `detector_summary.summary` + `anomalies/clustering.json` with context. This is deliberately simple and honest — a permutation refinement is out of scope v1.

**Acceptance check** — `pipeline/tests/test_composite.py`:
```python
def test_composite_is_mean_of_percentiles(test_db, seed_multi_detector):
    seed_multi_detector(test_db)          # known score vectors across 3 detectors
    composite.Composite().run(test_db)
    row = read_scores(test_db, "composite").sort("score", descending=True).head(1)
    d = row["detail"][0]
    assert abs(row["score"][0] - sum(d["percentiles"].values()) / d["n_detectors"]) < 1e-9

def test_planted_hot_zone_survives_bh(test_db, seed_zones):
    # 50 zones x 40 sections; one zone gets 10 of the 20 national "high" sections
    seed_zones(test_db, zones=50, per_zone=40, hot_zone_high=10, background_high=10)
    composite.Composite().run(test_db)
    surviving = composite.read_clusters(test_db)
    assert len(surviving) == 1 and surviving[0]["observed_high"] == 10

def test_uniform_highs_yield_no_clusters(test_db, seed_zones):
    seed_zones(test_db, zones=50, per_zone=40, hot_zone_high=0, background_high=20)
    composite.Composite().run(test_db)
    assert composite.read_clusters(test_db) == []
```
Run: `cd pipeline && uv run pytest tests/test_composite.py -v`. **Verify:** row 18 + re-run RR export so shards carry `composite`. **Boundaries:** runs after all per-section detectors; must tolerate a detector being absent (fewer percentiles), not crash.

---

### Task 19 — Overview page (national choropleth + headlines)
**Spec TODO:** Overview — national maps + headline findings.

**Files**
- `dashboard/src/pages/Overview/` (page, choropleth, headline cards) — create
- `dashboard/src/pages/Overview/overview.test.tsx` (pre-authored), topojson asset for BR municipalities (committed, source noted) — create
- `dashboard/src/i18n/*.json` — modify: keys (placeholder values)

**Work**
Intent: national municipality choropleth (topojson; color by a selectable metric: leading-candidate share / turnout / composite-flag density from `municipalities-index.json` + `summary.json`), headline cards from `summary.json` per-detector counts, each linking to its detector page; click municipality → drill-down route. Map rendering approach (d3-geo vs react-simple-maps) your choice; must stay static-bundle-only (no tile servers). Taste bar applies.

**Acceptance check** — `overview.test.tsx`:
```tsx
it("renders headline cards from summary.json with links to detector pages", async () => {
  renderWithProviders(<OverviewPage />);                  // fetch mocked with fixture summary
  const cards = await screen.findAllByTestId("headline-card");
  expect(cards.length).toBeGreaterThanOrEqual(3);
  expect(cards[0].querySelector("a")).toHaveAttribute("href", expect.stringContaining("/detectors/"));
});
it("choropleth renders one path per municipality in the index fixture", async () => {
  renderWithProviders(<OverviewPage />);
  const map = await screen.findByTestId("choropleth");
  expect(map.querySelectorAll("path[data-mun]").length).toBe(fixtureIndex.length);
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
Run the full pipeline: download all UFs, ingest (~100 M fact rows), analyze all detectors, export all ~5,570 shards, both gates green. Expected pain points (pre-authorized to fix, report each): ingest memory/batching, detector vectorization at national scale, São Paulo capital shard splitting. Record in `docs/baselines.md`: national quarantine rate, fact_votes count, per-stage wall-clock, shard-size distribution (p50/p95/max, before/after split), file count, total bytes, largest file. **Budget breaches are plan defects — stop and escalate; do not adjust budgets.**

**Acceptance check** — the existing integration suite at national scope (no new test file — the gates ARE the acceptance):
Run: `cd pipeline && make pipeline-all && uv run pytest -m integration -v` — reconciliation gate and export gate green against the national database, plus the full non-integration suite still green.

**Verify:** table row 23. **Boundaries:** no new features; no detector-logic changes (a detector that breaks at scale for statistical reasons — not perf — is a `blocked:` return).

---

### Task 24 — Cloudflare Pages deploy config + production smoke
**Spec TODO:** Zero cost to publish; free-tier hosting (Cloudflare Pages primary target). Immutable + aggressive CDN caching.

**Files**
- `dashboard/public/_headers` — create: `data-manifest.json` → `no-store`; every hashed file under `/data/` → `Cache-Control: public, max-age=31536000, immutable`
- `docs/deploy.md` — create: exact wrangler/Pages steps, env-free build, re-deploy procedure (new export = new hashed files + new manifest, no stale mixing by construction)
- `dashboard/vite.config.ts` — modify if needed for asset handling of `.json.gz`

**Work**
Intent: `npm run build` output + `public/data/` deploys as-is to Pages within limits (Task 23 baselines prove counts). Verify `.json.gz` files are served as opaque bytes (no double-decompression against the Task 11 client-side DecompressionStream) — if Pages applies Content-Encoding to `.gz` paths, adjust serving strategy (e.g. drop the `.gz` suffix, keep gzipped payload) and report the deviation. No secrets, no build-time env.

**Acceptance check:** `npm run build && npm run smoke` against `vite preview` of the production build with real national data linked into `public/data/` — loads, renders, zero console errors, one municipality drill-down fetch succeeds. Then a **codex-computer-use** pass against the deployed preview URL: load overview, switch locale, open a drill-down, screenshot evidence. Real-site verification is risk-gated and this is the user-visible release surface — required here.

**Verify:** table row 24. **Boundaries:** no CI/CD automation (out of scope v1); the final production deploy action is the user's.

---

## Self-review record

Spec coverage: every spec section maps to ≥ 1 task — ingestion rules → 2–5; detectors 1–8 → 6/6/7/13/14/15/16/17; composite+clustering → 18; export A/B + sharding care 1–5 → 8/9/10; dashboard pages → 12/19/20/21; bilingual copy → 11 (scaffold) + 22 (authoring); testing section → the acceptance tests themselves; hosting constraints → 10/23/24. Type consistency: `analysis.section_scores` DDL (Task 6) is the single scoring contract all detectors write; shard schema (Task 8) is the single contract Tasks 10/11/12/18 read; manifest shape (Task 10) is what Task 11's `resolve()` consumes. No placeholders: every task carries runnable acceptance assertions; implementer-authored test *fixtures* (seed helpers) are deliberate rung-appropriate freedom, with assertions fixed upstream.
