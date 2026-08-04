#!/usr/bin/env python
"""Step 01 — download the TSE source archives into `data/raw/`.

    uv run python 01_download.py --uf RR   # one federative unit
    uv run python 01_download.py           # all of Brazil
    uv run python 01_download.py --uf RR --dry-run

Idempotent: files whose recorded SHA-256 still matches are skipped.
"""

from __future__ import annotations

import argparse
import sys

from observatory import download, sources


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
    args = parser.parse_args(argv)

    uf = args.uf.upper() if args.uf else None
    if uf is not None and uf not in sources.UFS:
        parser.error(f"unknown UF {uf!r}; expected one of {', '.join(sources.UFS)}")

    if args.dry_run:
        print(f"would fetch into {download.RAW_DIR}:")
        for line in download.plan(uf):
            print("  " + line)
        return 0

    report = download.fetch_all_report(uf)
    print(
        f"raw dir: {download.RAW_DIR}\n"
        f"fetched: {len(report.fetched)}  skipped: {len(report.skipped)}  "
        f"adopted: {len(report.adopted)}  unresolved: {len(report.unresolved)}"
    )
    if report.unresolved:
        print(
            "blocked: urna_correspondence — could not resolve "
            + ", ".join(sorted(report.unresolved)),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
