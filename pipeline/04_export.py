#!/usr/bin/env python
"""Step 04 — export the dashboard's static data files.

    uv run python 04_export.py --uf RR                   # shards + aggregates
    uv run python 04_export.py --uf RR --shards-only     # per-municipality shards
    uv run python 04_export.py --uf RR --aggregates-only --out /tmp/data
    uv run python 04_export.py --uf RR --golden-shard 03158   # refresh the fixture

Writes two things under ``dashboard/public/data/``:

  ``shards/{mun_code}.json.gz``   one lazily-fetched file per municipality, or
                                  a split index plus per-zone subshards when a
                                  municipality breaches the 300 KiB target
                                  (see ``observatory.export.shards``)
  the aggregate files             ``summary.json``, ``states/{uf}.json``,
                                  ``anomalies/{detector}.json``,
                                  ``municipalities-index.json`` and
                                  ``annulled-candidacies.json``
                                  (see ``observatory.export.aggregates``)

``--uf`` scopes the SHARDS.  The aggregates are national by construction —
they describe whatever the warehouse currently holds — so a single-state
warehouse simply produces single-state aggregates.

Reads the warehouse and ``analysis.*``; writes nothing to the database.  Safe
to re-run: both writers are deterministic, so an unchanged warehouse produces
byte-identical files.

The validation gate + content-hash manifest (Task 10) land later.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from observatory import ingest
from observatory.config import REPO_ROOT, settings
from observatory.export import aggregates, shards

DEFAULT_OUT = REPO_ROOT / "dashboard" / "public" / "data"
GOLDEN_DIR = settings.fixtures_dir / "golden_shard"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uf", required=True, help="Federative unit, e.g. RR.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--shards-only",
        action="store_true",
        help="Export municipality shards only, skipping the aggregates.",
    )
    mode.add_argument(
        "--aggregates-only",
        action="store_true",
        help="Export the aggregate files only, leaving existing shards alone.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output root (default {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--golden-shard",
        metavar="CODE",
        help=(
            "Also write this municipality's shard to data/fixtures/golden_shard/ "
            "as the cross-language golden fixture (Task 11's TS decoder)."
        ),
    )
    args = parser.parse_args(argv)

    if args.golden_shard and args.aggregates_only:
        print("blocked: --golden-shard needs the shard pass.", file=sys.stderr)
        return 1

    uf = args.uf.upper()
    out_root = Path(args.out)
    db = ingest.connect()
    try:
        if not db.scalar("SELECT EXISTS (SELECT 1 FROM fact_votes)"):
            print(
                "blocked: the warehouse is empty — run `make pipeline UF=RR` first.",
                file=sys.stderr,
            )
            return 1

        if not args.aggregates_only:
            out_dir = out_root / "shards"
            started = time.perf_counter()
            print(f"export: {uf} shards -> {out_dir}")

            results: list[tuple[shards.MunicipalityData, shards.WriteResult]] = []
            for mun in shards.read_uf(db, uf):
                if not mun.sections:
                    print(f"  {mun.code} {mun.name}: no sections, skipped")
                    continue
                results.append((mun, shards.write_shard(mun, out_dir)))

            if not results:
                print(f"blocked: no municipalities found for UF {uf}.", file=sys.stderr)
                return 1

            _report(results, time.perf_counter() - started)

            if args.golden_shard:
                code = args.golden_shard
                match = next((m for m, _ in results if m.code == code), None)
                if match is None:
                    print(f"blocked: {code} is not a municipality of {uf}.",
                          file=sys.stderr)
                    return 1
                _write_golden(match)

        if not args.shards_only:
            started = time.perf_counter()
            print(f"\nexport: aggregates -> {out_root}")
            report = aggregates.write_all(db, out_root)
            _report_aggregates(report, time.perf_counter() - started)
            if report.unmapped:
                # Not fatal — a marker-less municipality is still searchable —
                # but it means the committed crosswalk missed a real code, and
                # that is a fixture defect, not a rendering detail.
                print(
                    f"  WARNING: {len(report.unmapped)} municipalities have no "
                    f"centroid: {', '.join(report.unmapped[:10])}"
                    f"{' …' if len(report.unmapped) > 10 else ''}",
                    file=sys.stderr,
                )
        return 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _report(
    results: list[tuple[shards.MunicipalityData, shards.WriteResult]],
    elapsed: float,
) -> None:
    print(f"\n  {'code':<7} {'name':<22} {'sections':>8} {'zones':>5} "
          f"{'races':>5} {'gz bytes':>10}  files")
    for mun, result in sorted(results, key=lambda r: -r[1].total_bytes):
        races = len({row.race for row in mun.stats} | {row.race for row in mun.votes})
        note = f"SPLIT into {len(result.paths) - 1} zones" if result.split else ""
        print(
            f"  {mun.code:<7} {mun.name[:22]:<22} {len(mun.sections):>8,} "
            f"{len(mun.zones):>5} {races:>5} {result.total_bytes:>10,}  {note}"
        )

    sizes = sorted(size for _, r in results for size in r.gz_sizes.values())
    files = len(sizes)
    splits = [mun.code for mun, r in results if r.split]
    print(
        f"\n  {len(results)} municipalities -> {files} files, "
        f"{sum(sizes):,} B total\n"
        f"  per-file gzipped: p50 {int(statistics.median(sizes)):,} B · "
        f"p90 {sizes[min(files - 1, int(files * 0.9))]:,} B · "
        f"max {sizes[-1]:,} B "
        f"(target {shards.SHARD_TARGET_BYTES:,} B, hard {shards.SHARD_HARD_BYTES:,} B)\n"
        f"  split municipalities: {', '.join(splits) if splits else 'none'}\n"
        f"  wall clock: {elapsed:.1f}s"
    )


def _report_aggregates(report: aggregates.ExportReport, elapsed: float) -> None:
    for name in sorted(report.sizes):
        print(f"  {name:<34} {report.sizes[name]:>10,} B")
    print(
        f"\n  races: "
        + " · ".join(f"{rid} {votes:,}" for rid, votes in report.races.items())
    )
    print(
        "  detectors: "
        + (" · ".join(f"{key} {scored:,} scored"
                      for key, scored in report.detectors.items()) or "none")
    )
    print(
        f"  {report.municipalities} municipalities "
        f"({report.municipalities - len(report.unmapped)} with centroids) · "
        f"annulled candidacies: {report.annulled_candidates} candidates, "
        f"{report.annulled_votes:,} votes\n"
        f"  {len(report.paths)} files, {report.total_bytes:,} B total · "
        f"wall clock: {elapsed:.1f}s"
    )


# --------------------------------------------------------------------------
# Golden fixture (Task 11's cross-language round trip)
# --------------------------------------------------------------------------


def _write_golden(mun: shards.MunicipalityData) -> None:
    """Commit one real shard plus the totals a TS decoder must reproduce.

    Deliberately a *small* municipality carrying every schema feature that
    matters — several races including a non-presidential one, a two-round
    race, sparse COO votes, dense stats, cross-race ``ALL_r`` scores and at
    least one ``score_detail`` entry.
    """
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    result = shards.write_shard(mun, GOLDEN_DIR)
    if result.split:
        raise SystemExit(
            f"blocked: {mun.code} splits, and a split index is the wrong shape "
            "for a decoder fixture — pick a smaller municipality."
        )
    doc = shards.encode_shard(mun)
    path = result.paths[0]

    expected = {
        "file": path.name,
        "v": doc["v"],
        "code": doc["code"],
        "name": doc["name"],
        "uf": doc["uf"],
        "gzBytes": result.gz_sizes[path.name],
        "sections": len(doc["zone"]),
        "races": doc["races"],
        "candidateCounts": {r: len(c) for r, c in doc["cand"].items()},
        # The headline assertion: COO triplets must sum to this per race.
        "pres1Total": sum(doc["votes"]["PRES_1"]["n"]),
        "raceTotals": {r: sum(v["n"]) for r, v in doc["votes"].items()},
        "voteEntries": {r: len(v["n"]) for r, v in doc["votes"].items()},
        # Dense arrays: a decoder that mis-aligns them fails these.
        "statsTotals": {
            r: {name: sum(col) for name, col in s.items()}
            for r, s in doc["stats"].items()
        },
        "scoreKeys": {r: sorted(keys) for r, keys in doc["scores"].items()},
        "scoredCounts": {
            r: {k: sum(1 for v in col if v is not None) for k, col in keys.items()}
            for r, keys in doc["scores"].items()
        },
        "scoreDetailSections": {
            r: sorted(entries, key=int) for r, entries in doc["score_detail"].items()
        },
        "firstSection": {"zone": doc["zone"][0], "section": doc["section"][0]},
        "lastSection": {"zone": doc["zone"][-1], "section": doc["section"][-1]},
    }
    (GOLDEN_DIR / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"\n  golden fixture: {path} ({result.gz_sizes[path.name]:,} B) "
        f"+ {GOLDEN_DIR / 'expected.json'}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
