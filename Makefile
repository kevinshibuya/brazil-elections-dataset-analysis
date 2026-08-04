.PHONY: pipeline pipeline-all reconcile analyze test-pipeline test-dashboard smoke

# Run the full pipeline for a single federative unit, e.g. `make pipeline UF=RR`.
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
	@echo "make pipeline UF=$(UF): download + ingest + reconciliation gate + detectors; export not implemented yet (Tasks 8-10)"

# Re-run the reconciliation gate over whatever is already loaded.
reconcile:
	cd pipeline && uv run python 02_ingest.py --gate-only

# Re-run the detectors over whatever is already loaded.  Idempotent: each
# detector replaces its own rows in `analysis.*`.  `make analyze ONLY=t1_arithmetic`
# runs a subset.
analyze:
	cd pipeline && uv run python 03_analyze.py $(if $(ONLY),--only $(ONLY))

# Run the pipeline for all of Brazil.
pipeline-all:
	@echo "make pipeline-all: not implemented yet (Task 23)" >&2
	@exit 1

test-pipeline:
	cd pipeline && uv run pytest -q

test-dashboard:
	cd dashboard && npm run test -- --run

smoke:
	cd dashboard && npm run smoke
