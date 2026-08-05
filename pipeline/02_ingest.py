#!/usr/bin/env python
"""Step 02 — stream the raw TSE archives into Postgres, atomically.

    uv run python 02_ingest.py --uf RR   # one federative unit, replacing the warehouse
    uv run python 02_ingest.py           # all of Brazil
    uv run python 02_ingest.py --uf AP --append     # ... alongside what is already loaded
    uv run python 02_ingest.py --uf RR --plan       # list sources, touch nothing
    uv run python 02_ingest.py --gate-only          # re-run the gate, load nothing

The whole run is one transaction: if the quarantine gate (> 0.01 % unparseable
rows) or any integrity check fails, nothing is committed and the previously
loaded data is still there.

By default a run TRUNCATES the warehouse first, so it replaces whatever was
loaded before.  `--append` skips that truncate and loads INTO what is there --
which is the only way a UF-by-UF national pass can work, because the Task 23
storage lever means the 28 members arrive as 28 separate invocations and a
resetting one would destroy its 27 predecessors.  `05_pipeline_all.py` resets
on the first member and appends for the rest.

The reconciliation gate (Task 5) runs automatically AFTER every load, once the
load has committed -- deliberately outside the run's transaction, so that a
failing gate leaves its ``reconciliation_report`` behind to be read.  A gate
failure is a non-zero exit code.

That gate is SCOPED TO `--uf`.  Reconciliation keys on `mun_code` and a
municipality belongs to exactly one UF, so it is additive across members: a
state that reconciled when it landed cannot stop reconciling because a later
state arrived.  Re-comparing the whole warehouse after every member cost more
than the loads themselves by the tenth of 28 members.  Fail-fast is unchanged
-- each member is still gated as it lands, and a bad member still stops the
pass at its own step.  Omit `--uf` (or run `--gate-only` without one) for the
unscoped national pass over everything loaded.
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
        "rows live only there. 'ZZ' (exterior) reads ONLY those national "
        "members: there is no per-UF exterior archive, so a UF-by-UF national "
        "load needs this extra pass or it drops every exterior section.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=ingest.DEFAULT_BATCH_SIZE,
        help="Rows per streamed batch (memory/throughput trade-off).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Load INTO the existing warehouse instead of truncating it first. "
        "Required for every member after the first of a UF-by-UF national pass. "
        "Distinct UFs cannot collide (every natural key is scoped by "
        "mun_code); re-appending a UF already loaded raises a duplicate-key "
        "failure, deliberately, rather than double-counting it. Analysis "
        "output is still cleared — a score is a statement about a particular "
        "warehouse and this changes the warehouse.",
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
        "already in the warehouse. Honours --uf: with it the gate compares "
        "that member only, without it every municipality loaded.",
    )
    args = parser.parse_args(argv)

    loadable = (*sources.UFS, ingest.EXTERIOR)
    uf = args.uf.upper() if args.uf else None
    if uf is not None and uf not in loadable:
        parser.error(f"unknown UF {uf!r}; expected one of {', '.join(loadable)}")

    if args.plan:
        for source in ingest.plan_sources(uf):
            print(f"  {source.kind:14s} {source.label}")
        return 0

    started = time.perf_counter()
    db = ingest.connect()
    try:
        if not args.gate_only:
            mode = "append" if args.append else "reset"
            print(
                f"ingest: uf={uf or 'ALL'} batch_size={args.batch_size:,} "
                f"mode={mode}"
            )
            try:
                report = ingest.run(
                    uf,
                    batch_size=args.batch_size,
                    log=print,
                    db=db,
                    reset=not args.append,
                )
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

        return _reconcile(db, uf)
    finally:
        db.close()


def _reconcile(db, uf: str | None) -> int:
    """The reconciliation gate, run over the member this invocation loaded.

    `uf` is the scope, not just a label: with it the gate compares that
    member's municipalities and leaves every other member's findings in
    `reconciliation_report` untouched.  Without it, the whole warehouse.
    """
    scope = uf or "the whole warehouse"
    print(f"\nreconciliation gate (tolerance zero) — scope: {scope}:")
    t0 = time.perf_counter()
    result = reconcile.run_gate(db, uf=uf, raise_on_failure=False)
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
            f"\nblocked: reconciliation gate ({scope}) — our aggregates do not "
            "equal TSE's published totals.\n"
            f"Read the diagnostic: SELECT * FROM {reconcile.REPORT_TABLE};",
            file=sys.stderr,
        )
        return 1
    print("\nreconciliation gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
