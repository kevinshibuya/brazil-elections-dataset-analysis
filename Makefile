.PHONY: pipeline pipeline-all reconcile test-pipeline test-dashboard smoke

# Run the full pipeline for a single federative unit, e.g. `make pipeline UF=RR`.
# 02_ingest.py runs the reconciliation gate itself, right after the load
# commits, and exits non-zero if our totals do not equal TSE's.
pipeline:
ifndef UF
	$(error UF is required, e.g. `make pipeline UF=RR`)
endif
	cd pipeline && uv run python 01_download.py --uf $(UF)
	cd pipeline && uv run python 02_ingest.py --uf $(UF)
	@echo "make pipeline UF=$(UF): download + ingest + reconciliation gate green; analysis and export not implemented yet (Tasks 6-10)"

# Re-run the reconciliation gate over whatever is already loaded.
reconcile:
	cd pipeline && uv run python 02_ingest.py --gate-only

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
