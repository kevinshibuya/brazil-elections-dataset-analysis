#!/usr/bin/env python
"""Step 02 — stream the raw TSE archives into Postgres, atomically.

    uv run python 02_ingest.py --uf RR   # one federative unit
    uv run python 02_ingest.py           # all of Brazil
    uv run python 02_ingest.py --uf RR --plan       # list sources, touch nothing
    uv run python 02_ingest.py --gate-only          # re-run the gate, load nothing

The whole run is one transaction: if the quarantine gate (> 0.01 % unparseable
rows) or any integrity check fails, nothing is committed and the previously
loaded data is still there.

The reconciliation gate (Task 5) runs automatically AFTER every load, once the
load has committed -- deliberately outside the run's transaction, so that a
failing gate leaves its ``reconciliation_report`` behind to be read.  A gate
failure is a non-zero exit code.
"""

from __future__ import annotations

import argparse
import sys
import time

from observatory import ingest, reconcile, sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uf",
        default=None,
        help="Federative unit to ingest (e.g. RR). Omit for all of Brazil. "
        "The national BR members are always read — presidential and exterior "
        "rows live only there.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=ingest.DEFAULT_BATCH_SIZE,
        help="Rows per streamed batch (memory/throughput trade-off).",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="List the archive members a run would read, then exit.",
    )
    parser.add_argument(
        "--gate-only",
        action="store_true",
        help="Skip the load and re-run the reconciliation gate over what is "
        "already in the warehouse.",
    )
    args = parser.parse_args(argv)

    uf = args.uf.upper() if args.uf else None
    if uf is not None and uf not in sources.UFS:
        parser.error(f"unknown UF {uf!r}; expected one of {', '.join(sources.UFS)}")

    if args.plan:
        for source in ingest.plan_sources(uf):
            print(f"  {source.kind:14s} {source.label}")
        return 0

    started = time.perf_counter()
    db = ingest.connect()
    try:
        if not args.gate_only:
            print(f"ingest: uf={uf or 'ALL'} batch_size={args.batch_size:,}")
            try:
                report = ingest.run(uf, batch_size=args.batch_size, log=print, db=db)
            except ingest.QuarantineGateError as exc:
                print(f"blocked: quarantine gate — {exc}", file=sys.stderr)
                return 1
            except ingest.IngestError as exc:
                print(f"blocked: ingest — {exc}", file=sys.stderr)
                return 1

            elapsed = time.perf_counter() - started
            rate = report.rows_attempted / elapsed if elapsed else 0.0
            print(
                f"\nrows attempted : {report.rows_attempted:,}\n"
                f"rows loaded    : {report.rows_loaded:,}\n"
                f"rows quarantined: {report.rows_quarantined:,}\n"
                f"rows out of scope: {report.rows_out_of_scope:,}\n"
                f"quarantine rate: {report.quarantine_rate:.6%} "
                f"(gate {ingest.QUARANTINE_GATE:.4%})\n"
                f"wall clock     : {elapsed:.1f}s ({rate:,.0f} rows/s attempted)"
            )
            print("\ntable row counts:")
            for table, n in sorted(ingest.counts(db).items()):
                print(f"  {table:24s} {n:>12,}")
            print(f"\nelection ids loaded: {sorted(ingest.distinct_election_ids(db))}")

        return _reconcile(db)
    finally:
        db.close()


def _reconcile(db) -> int:
    """The reconciliation gate, run over the committed warehouse."""
    print("\nreconciliation gate (tolerance zero):")
    t0 = time.perf_counter()
    result = reconcile.run_gate(db, raise_on_failure=False)
    print(f"  keys compared [{time.perf_counter() - t0:.1f}s]:")
    for name, keys in sorted(result.keys_compared.items()):
        print(f"    {name:22s} {keys:>10,}")
    print("  report rows by category:")
    if not result.categories:
        print("    (none — every comparison matched to the vote)")
    for category in result.categories:
        flag = "FATAL" if category.fatal else "finding"
        print(
            f"    {category.category:22s} {category.rows:>7,} row(s) "
            f"{category.votes:>10,} votes  [{flag}]"
        )
    if not result.ok:
        print(
            "\nblocked: reconciliation gate — our aggregates do not equal TSE's "
            "published totals.\n"
            f"Read the diagnostic: SELECT * FROM {reconcile.REPORT_TABLE};",
            file=sys.stderr,
        )
        return 1
    print("\nreconciliation gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
