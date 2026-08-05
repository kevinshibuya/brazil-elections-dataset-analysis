#!/usr/bin/env python
"""Step 05 — the all-Brazil run (Task 23).  Driven by `make pipeline-all`.

    uv run python 05_pipeline_all.py                      # all 27 states + exterior
    uv run python 05_pipeline_all.py --dry-run            # the plan, no network, no DB
    uv run python 05_pipeline_all.py --only RR --dry-run  # ordering on one state
    uv run python 05_pipeline_all.py --only RR,ZZ         # a real, small run

THE ORDERING IS THE DISK BUDGET.  It is not an implementation detail:

  0. Pre-flight, BEFORE the first byte is downloaded.  A run that cannot finish
     must not start; discovering that at hour three costs the run and leaves a
     half-loaded warehouse behind.
  1. The national archives, once (`observatory.sources.national_datasets`).
     ~1.02 GiB that stays on disk for the whole run — every UF's ingest reads
     `votacao_secao_2022_BR.zip` for its presidential and exterior rows (A2),
     so it is NEVER reclaimed.
  2. Then, per UF: download that one state's section archive -> ingest it (the
     reconciliation gate runs inside 02_ingest.py; a failure is a non-zero
     exit) -> reclaim the archive.  One at a time: downloading all 27 up front
     and deleting afterwards would peak at ~3.4 GiB and defeat the lever
     entirely.  Peaking at the largest single archive costs ~0.70 GiB.
  3. Then 03_analyze.py ONCE over the whole warehouse — the detectors are
     national comparisons and must see every state.
  4. Then 04_export.py per UF (`--shards-only`, which carries the other states'
     shards forward instead of pruning them), and finally one
     `--aggregates-only` pass whose validation gate is the run's last word.
  5. Fail fast: a non-zero exit from any stage stops the run.  A half-loaded
     warehouse that keeps going is worse than one that stops.

THE EXTERIOR PASS IS NOT OPTIONAL.  `02_ingest.py --uf XX` filters every source
to `uf = 'XX'`, so looping over the 27 states loads no exterior rows at all —
they live only in the `_BR` members, and only a `--uf ZZ` pass picks them up.
It runs FIRST because it reads nothing but the national archives: if the
BR-members path is broken, that fails in minutes instead of hours.

`build_actions` is the single definition of the ordering: `--dry-run` prints
that list and a real run executes it, so the two cannot drift apart.

Each script stage runs as its own subprocess.  That keeps the existing scripts
as the single definition of what a stage does, and hands the OS a clean slate
between states rather than accumulating 27 states' worth of allocator debris.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from observatory import download, ingest, sources, storage

HERE = Path(__file__).resolve().parent

#: The exterior member: ingested and exported, never downloaded (no archive).
EXTERIOR = ingest.EXTERIOR

SELECTABLE: tuple[str, ...] = (*sources.UFS, EXTERIOR)


# --------------------------------------------------------------------------
# Actions — the ordering, once
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """One step of the run.

    `kind` is what to do; `label` is what the journal and the dry run call it;
    `argv` is set for script stages and `uf` for the download/reclaim steps.
    """

    kind: str  # "download-national" | "download-uf" | "script" | "reclaim"
    label: str
    argv: tuple[str, ...] = ()
    uf: str = ""

    def describe(self) -> str:
        if self.kind == "script":
            return f"$ python {' '.join(self.argv)}"
        if self.kind == "download-national":
            return ", ".join(
                d.filename or d.name for d in sources.national_datasets()
            )
        if self.kind == "download-uf":
            return sources.votes_section(self.uf).filename or ""
        return (
            f"delete {sources.votes_section(self.uf).filename} "
            f"(~{storage.uf_archive_bytes(self.uf) / storage.GIB:.3f} GiB)"
        )


def build_actions(
    ufs: list[str], *, batch_size: int | None = None, keep_archives: bool = False
) -> list[Action]:
    """Every step of a run over `ufs`, in the order the disk budget requires."""
    ingest_flags = ("--batch-size", str(batch_size)) if batch_size else ()
    actions: list[Action] = [
        Action("download-national", "download national archives")
    ]
    for uf in ufs:
        if uf != EXTERIOR:
            actions.append(Action("download-uf", f"download {uf}", uf=uf))
        actions.append(
            Action("script", f"ingest {uf}",
                   ("02_ingest.py", "--uf", uf, *ingest_flags))
        )
        if uf != EXTERIOR and not keep_archives:
            actions.append(Action("reclaim", f"reclaim {uf}", uf=uf))

    actions.append(Action("script", "analyze (national)", ("03_analyze.py",)))
    for uf in ufs:
        actions.append(
            Action("script", f"export {uf} shards",
                   ("04_export.py", "--uf", uf, "--shards-only"))
        )
    # `--aggregates-only` ignores --uf (the aggregates are national by
    # construction) but the flag is required, so it gets the last member.
    actions.append(
        Action("script", "export aggregates + validation gate",
               ("04_export.py", "--uf", ufs[-1], "--aggregates-only"))
    )
    return actions


def _order(selected: set[str]) -> list[str]:
    """Exterior first (fails fast, needs no download), then the states."""
    states = [uf for uf in sources.UFS if uf in selected]
    return ([EXTERIOR] if EXTERIOR in selected else []) + states


# --------------------------------------------------------------------------
# Run bookkeeping
# --------------------------------------------------------------------------


@dataclass
class Journal:
    """Per-stage wall clock and reclaimed bytes — the run's own baselines.

    `docs/baselines.md` wants both. A run that does not report them is a run
    that has to happen twice.
    """

    stages: list[tuple[str, float]] = field(default_factory=list)
    reclaimed: list[tuple[str, int]] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def stage(self, label: str, elapsed: float) -> None:
        self.stages.append((label, elapsed))
        print(f"--- {label}: {elapsed:,.1f}s", flush=True)

    def reclaim(self, uf: str, freed: int) -> None:
        self.reclaimed.append((uf, freed))
        total = sum(n for _, n in self.reclaimed)
        print(
            f"--- reclaim {uf}: freed {freed:,} B "
            f"({freed / storage.GIB:.3f} GiB) · run total "
            f"{total:,} B ({total / storage.GIB:.3f} GiB)",
            flush=True,
        )

    def report(self) -> None:
        print("\n" + "=" * 72)
        print("per-stage wall clock")
        print("=" * 72)
        for label, elapsed in self.stages:
            print(f"  {label:<38} {elapsed:>10,.1f}s")
        print(f"  {'TOTAL':<38} {time.perf_counter() - self.started:>10,.1f}s")
        if self.reclaimed:
            total = sum(n for _, n in self.reclaimed)
            print("\nreclaimed per-UF archives")
            for uf, freed in self.reclaimed:
                print(f"  {uf:<38} {freed:>14,} B")
            print(
                f"  {'TOTAL':<38} {total:>14,} B "
                f"({total / storage.GIB:.2f} GiB never held at once)"
            )


class StageFailed(RuntimeError):
    """A stage exited non-zero.  Carries the exit code the run should return."""

    def __init__(self, label: str, code: int) -> None:
        super().__init__(f"{label} exited {code}")
        self.label = label
        self.code = code


def _banner(text: str) -> None:
    print(f"\n{'=' * 72}\n== {text}\n{'=' * 72}", flush=True)


def _execute(action: Action, journal: Journal) -> None:
    """Run one action, timed.  A non-zero script exit aborts the whole run."""
    if action.kind == "reclaim":
        journal.reclaim(action.uf, storage.reclaim_uf_archive(action.uf))
        return

    _banner(f"{action.label}\n== {action.describe()}")
    started = time.perf_counter()

    if action.kind == "script":
        completed = subprocess.run([sys.executable, *action.argv], cwd=HERE)  # noqa: S603
        journal.stage(action.label, time.perf_counter() - started)
        if completed.returncode != 0:
            raise StageFailed(action.label, completed.returncode)
        return

    datasets = (
        sources.national_datasets()
        if action.kind == "download-national"
        else [sources.votes_section(action.uf)]
    )
    report = download.fetch_report(datasets)
    print(f"fetched {len(report.fetched)}  skipped {len(report.skipped)}  "
          f"adopted {len(report.adopted)}")
    journal.stage(action.label, time.perf_counter() - started)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--only",
        metavar="UF[,UF...]",
        help="Restrict the run to these members (default: all 27 states plus "
        f"{EXTERIOR}). Exercises the same ordering on a subset.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the pre-flight budget and every action in order, then exit. "
        "Touches no network, no database and no files.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Run even if the free-disk estimate says it cannot finish. "
        "For when you know something the estimate does not.",
    )
    parser.add_argument(
        "--keep-archives",
        action="store_true",
        help="Do not reclaim per-UF archives after ingest. Costs ~3.4 GiB "
        "nationally; useful when re-running ingest without re-downloading.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Passed through to 02_ingest.py (memory/throughput trade-off).",
    )
    args = parser.parse_args(argv)

    if args.only:
        requested = [n.strip().upper() for n in args.only.split(",") if n.strip()]
        unknown = [name for name in requested if name not in SELECTABLE]
        if unknown:
            parser.error(
                f"unknown member(s) {', '.join(unknown)}; "
                f"expected from {', '.join(SELECTABLE)}"
            )
        ufs = _order(set(requested))
    else:
        ufs = _order(set(SELECTABLE))

    required = storage.peak_bytes(ufs)
    actions = build_actions(
        ufs, batch_size=args.batch_size, keep_archives=args.keep_archives
    )

    print(f"pipeline-all: {len(ufs)} member(s) — {', '.join(ufs)}")
    print(_budget_line(required))

    if args.dry_run:
        return _dry_run(ufs, actions)

    if args.skip_preflight:
        print("pre-flight: SKIPPED (--skip-preflight)")
    else:
        try:
            storage.preflight(required)
        except storage.InsufficientDisk as exc:
            print(f"\nblocked: pre-flight — {exc}", file=sys.stderr)
            return 1

    journal = Journal()
    try:
        for action in actions:
            _execute(action, journal)
    except StageFailed as exc:
        journal.report()
        print(
            f"\nblocked: {exc.label} exited {exc.code} — stopping here. "
            "The warehouse holds whatever committed before this point; "
            "re-run after fixing the stage (every stage is idempotent, and a "
            "reclaimed archive is re-downloaded).",
            file=sys.stderr,
        )
        return exc.code

    journal.report()
    free = storage.free_bytes(download.RAW_DIR.parent)
    print(
        f"\nfree disk now: {free / storage.GIB:,.2f} GiB "
        f"(the estimate put this run's peak at {required / storage.GIB:,.2f} GiB)\n"
        "pipeline-all: download + ingest + reconciliation gate + detectors + "
        "shards + aggregates + manifest + export validation gate — all green.\n"
        "Record the wall clock, reclaimed bytes and gate output above in "
        "docs/baselines.md."
    )
    return 0


def _budget_line(required: int) -> str:
    free = storage.free_bytes(download.RAW_DIR.parent)
    budget = required * (1 + storage.HEADROOM)
    verdict = "OK" if free >= budget else "SHORT"
    return (
        f"pre-flight: peak {required / storage.GIB:,.2f} GiB "
        f"+ {storage.HEADROOM:.0%} headroom = {budget / storage.GIB:,.2f} GiB "
        f"required, {free / storage.GIB:,.2f} GiB free — {verdict}"
    )


def _dry_run(ufs: list[str], actions: list[Action]) -> int:
    print(f"raw dir: {download.RAW_DIR}\n")
    for n, action in enumerate(actions, 1):
        print(f"  {n:>3}. {action.label:<38} {action.describe()}")

    downloads = [uf for uf in ufs if uf != EXTERIOR]
    if downloads:
        largest = max(downloads, key=storage.uf_archive_bytes)
        held = storage.uf_archive_bytes(largest)
        upfront = sum(storage.uf_archive_bytes(uf) for uf in downloads)
        print(
            f"\nper-UF archives: at most ONE on disk at a time — peak {largest} "
            f"~{held / storage.GIB:.3f} GiB, vs ~{upfront / storage.GIB:.3f} GiB "
            "if all were downloaded up front. That difference is the lever."
        )
    print("\n(dry run — nothing downloaded, ingested, exported or deleted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
