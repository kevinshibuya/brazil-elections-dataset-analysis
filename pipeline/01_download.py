#!/usr/bin/env python
"""Step 01 — download the TSE source archives into `data/raw/`.

    uv run python 01_download.py --uf RR   # one federative unit
    uv run python 01_download.py           # all of Brazil
    uv run python 01_download.py --uf RR --dry-run

Idempotent: files whose recorded SHA-256 still matches are skipped.

A FREE-DISK PRE-FLIGHT RUNS FIRST (Task 23).  Downloading is the first thing
that spends disk, so it is where the gate belongs: a run that cannot finish
must not start.  `--uf XX` is gated on that one state's estimated footprint,
a bare run on the whole national one.  See `observatory.storage` for the
arithmetic and for what "free" means on APFS.
"""

from __future__ import annotations

import argparse
import sys

from observatory import download, sources, storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uf",
        default=None,
        help="Federative unit to narrow the per-UF files to (e.g. RR). "
        "National files are downloaded regardless.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files a run would fetch and exit, without any network access.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Download even if the free-disk estimate says the run cannot "
        "finish. For when you know something the estimate does not.",
    )
    args = parser.parse_args(argv)

    uf = args.uf.upper() if args.uf else None
    if uf is not None and uf not in sources.UFS:
        parser.error(f"unknown UF {uf!r}; expected one of {', '.join(sources.UFS)}")

    if args.dry_run:
        print(f"would fetch into {download.RAW_DIR}:")
        for line in download.plan(uf):
            print("  " + line)
        print(f"\n{_budget_line(uf)}")
        return 0

    if not args.skip_preflight:
        print(_budget_line(uf))
        try:
            storage.preflight(_required_bytes(uf))
        except storage.InsufficientDisk as exc:
            print(f"blocked: pre-flight — {exc}", file=sys.stderr)
            return 1

    report = download.fetch_all_report(uf)
    print(
        f"raw dir: {download.RAW_DIR}\n"
        f"fetched: {len(report.fetched)}  skipped: {len(report.skipped)}  "
        f"adopted: {len(report.adopted)}"
    )
    return 0


def _required_bytes(uf: str | None) -> int:
    """The footprint the pre-flight demands for this run."""
    return storage.NATIONAL_PEAK_BYTES if uf is None else storage.peak_bytes([uf])


def _budget_line(uf: str | None) -> str:
    required = _required_bytes(uf)
    return (
        f"disk pre-flight ({uf or 'ALL'}): "
        f"peak {required / storage.GIB:,.1f} GiB "
        f"+ {storage.HEADROOM:.0%} headroom = "
        f"{required * (1 + storage.HEADROOM) / storage.GIB:,.1f} GiB required, "
        f"{storage.free_bytes(download.RAW_DIR.parent) / storage.GIB:,.1f} GiB free"
    )


if __name__ == "__main__":
    raise SystemExit(main())
