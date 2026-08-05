.PHONY: pipeline pipeline-all reconcile analyze export test-pipeline test-dashboard smoke

# Run the full pipeline for a single federative unit, e.g. `make pipeline UF=RR`.
# 01_download.py runs the free-disk pre-flight before fetching anything, sized
# for that one state -- the same gate `pipeline-all` uses, with the smaller
# number.  A run that cannot finish must not start.
# 02_ingest.py runs the reconciliation gate itself, right after the load
# commits, and exits non-zero if our totals do not equal TSE's.  The detectors
# run only once that gate is green: scoring numbers we have not reconciled
# would be publishing an opinion about data we do not trust.
pipeline:
ifndef UF
	$(error UF is required, e.g. `make pipeline UF=RR`)
endif
	cd pipeline && uv run python 01_download.py --uf $(UF)
	cd pipeline && uv run python 02_ingest.py --uf $(UF)
	$(MAKE) analyze
	$(MAKE) export UF=$(UF)
	@echo "make pipeline UF=$(UF): download + ingest + reconciliation gate + detectors + shards + aggregates + manifest + export validation gate — all green"

# Re-run the reconciliation gate over whatever is already loaded.
reconcile:
	cd pipeline && uv run python 02_ingest.py --gate-only

# Re-run the detectors over whatever is already loaded.  Idempotent: each
# detector replaces its own rows in `analysis.*`.  `make analyze ONLY=t1_arithmetic`
# runs a subset.
analyze:
	cd pipeline && uv run python 03_analyze.py $(if $(ONLY),--only $(ONLY))

# Write the dashboard's static data files for one UF, e.g. `make export UF=RR`.
# Per-municipality shards for that UF, the (national) aggregate files, the
# content-hash manifest, and then the export validation gate — which re-reads
# every file from disk and fails the run if anything disagrees with Postgres.
# Deterministic and idempotent — re-running over an unchanged warehouse rewrites
# byte-identical files under identical hashed names.
export:
ifndef UF
	$(error UF is required, e.g. `make export UF=RR`)
endif
	cd pipeline && uv run python 04_export.py --uf $(UF)

# Run the pipeline for all of Brazil (Task 23).
#
#   make pipeline-all              the whole country: 27 states + the exterior
#   make pipeline-all DRY=1        print the ordered plan and the disk budget
#   make pipeline-all ONLY=RR,ZZ   the same ordering over a subset
#
# 05_pipeline_all.py owns the ordering, and the ordering IS the disk budget:
# pre-flight -> national archives once -> per UF (download -> ingest ->
# RECLAIM that archive) -> analyze -> export -> validation gate.  Holding one
# state archive at a time instead of all 27 is what brings the national peak
# down from ~28 GiB to ~25 GiB.  It prints per-stage wall clock and reclaimed
# bytes for docs/baselines.md.
pipeline-all:
	cd pipeline && uv run python 05_pipeline_all.py $(if $(ONLY),--only $(ONLY)) $(if $(DRY),--dry-run)

test-pipeline:
	cd pipeline && uv run pytest -q

test-dashboard:
	cd dashboard && npm run test -- --run

smoke:
	cd dashboard && npm run smoke
