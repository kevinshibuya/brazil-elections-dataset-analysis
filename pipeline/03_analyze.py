#!/usr/bin/env python
"""Step 03 — run the anomaly detectors over the loaded warehouse.

    uv run python 03_analyze.py                        # every detector
    uv run python 03_analyze.py --only t1_arithmetic   # a subset (repeatable)
    uv run python 03_analyze.py --list                 # what is registered
    uv run python 03_analyze.py --summary              # last run's summaries

Each detector is atomic on its own: it replaces its own rows in
``analysis.section_scores`` or leaves the previous run's untouched.  The run
as a whole is deliberately NOT one transaction — detectors are independent
measurements, and one failing must not throw away the others' work.

Reads the warehouse, writes only ``analysis.*``.  Safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from observatory import detectors, ingest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        metavar="NAME",
        help="Run just this detector (repeatable, or comma-separated).",
    )
    parser.add_argument(
        "--list", action="store_true", help="List registered detectors and exit."
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print the stored summary of each detector selected, in full.",
    )
    args = parser.parse_args(argv)

    if args.list:
        for detector in detectors.all():
            print(f"  {detector.name}")
        return 0

    only: list[str] | None = None
    if args.only:
        only = [name for arg in args.only for name in arg.split(",") if name]

    db = ingest.connect()
    try:
        try:
            selected = detectors.select(only)
        except KeyError as exc:
            print(f"blocked: {exc.args[0]}", file=sys.stderr)
            return 1

        if not db.scalar("SELECT EXISTS (SELECT 1 FROM fact_votes)"):
            print(
                "blocked: the warehouse is empty — run `make pipeline UF=RR` first.",
                file=sys.stderr,
            )
            return 1

        if args.summary:
            return _print_summaries(db, [d.name for d in selected])

        print(f"analyze: {len(selected)} detector(s)")
        started = time.perf_counter()
        runs = detectors.run(db, only=only, log=None)
        for name in (d.name for d in selected):
            record = runs[name]
            print(
                f"  {record.name:16s} {record.sections_scored:>9,} sections scored  "
                f"{record.sections_flagged:>7,} flagged  "
                f"{record.rows_scored:>10,} rows  [{record.elapsed_s:.1f}s]"
            )
        print(f"\nwall clock: {time.perf_counter() - started:.1f}s")
        return _print_summaries(db, [d.name for d in selected])
    finally:
        db.close()


def _print_summaries(db, names: list[str]) -> int:
    frame = detectors.read_summary(db)
    if frame.height == 0:
        print("\n(no detector summaries stored yet)")
        return 0
    # params/summary arrive as JSON text on purpose (see base.read_summary):
    # detectors share no key set, so decoding is per row.
    rows = {row["detector"]: row for row in frame.iter_rows(named=True)}
    for name in names:
        row = rows.get(name)
        if row is None:
            continue
        params = json.loads(row["params"] or "{}")
        summary = json.loads(row["summary"] or "{}")
        print(f"\n--- {name} ---")
        print(f"params : {json.dumps(params, ensure_ascii=False)}")
        print(f"summary: {json.dumps(summary, ensure_ascii=False, indent=2)}")
        payloads = detectors.read_state_payloads(db, name)
        if payloads.height:
            print(f"state payloads: {payloads.height} UF(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
