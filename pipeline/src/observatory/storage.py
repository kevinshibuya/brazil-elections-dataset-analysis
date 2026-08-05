"""Disk budget for the national run: a pre-flight gate and an archive reclaimer.

The all-Brazil pass does not fit on this machine unmitigated.  Two levers close
the gap, and both have to be *behaviour the pipeline carries* rather than
something an operator remembers to do at 2 a.m.:

* :func:`preflight` — refuse to start a run that cannot finish.
* :func:`reclaim_uf_archive` — delete each per-UF section archive the moment it
  is safely ingested, so the 27 state archives cost **one** file's worth of disk
  instead of all 27 at once (≈ 3.4 GiB → ≈ 0.70 GiB, the largest single one).

WHAT "FREE" MEANS ON macOS/APFS
    :func:`free_bytes` reports `shutil.disk_usage(path).free`, which is the
    *allocatable* free space.  This is deliberately **not** the number Finder
    shows.  Finder adds "purgeable" space — local Time Machine snapshots and
    other reclaimable APFS blocks — to its Available figure, and that space is
    not allocatable until macOS decides to reclaim it.  `disk_usage().free`
    agrees with `df` and with the APFS container's "not allocated" figure, and
    that is the only number a gate may use: relying on automatic purging
    part-way through is how a multi-hour job dies at hour three, with a
    half-loaded warehouse behind it.

NATIONAL FOOTPRINT ARITHMETIC (peak ≈ 25.2 GiB)
    Derived from `docs/baselines.md` — the measured RR run scaled by TSE's own
    published section counts (`QT_TOTAL_SECOES` in `detalhe_votacao_munzona_2022`,
    PRES round 1, non-transit), which is :data:`SECTIONS_BY_UF` below.

    Per-section unit costs, measured on RR (1,387 sections)::

        Postgres   52,067,351 B / 1,387 =  37,540 B/section
        raw zip     7,723,169 B / 1,387 =   5,568 B/section
        export        371,749 B / 1,387 =     268 B/section

    Everything that scales with *votável-rows* rather than sections carries the
    ``× 1.32`` ballot-breadth correction (:data:`BALLOT_BREADTH_CORRECTION`):
    RR runs one of the narrowest candidate fields in Brazil, so a pure
    section-count scale-up under-counts states like SP.  The peak is then::

        Postgres     37,540 × 496,238 × 1.32  ≈ 22.90 GiB   (all 28 UF members incl. ZZ)
        cluster overhead (WAL + catalogs)     ≈  0.39 GiB   (does not scale — max_wal_size)
        raw national archives (never reclaimed) ≈ 1.02 GiB
        largest single per-UF archive (SP)
                      5,568 × 101,628 × 1.32  ≈  0.70 GiB
        exported shards + aggregates
                        268 × 496,238 × 1.32  ≈  0.16 GiB
                                              -----------
        PEAK                                  ≈ 25.17 GiB
        + 20 % headroom                       ≈ 30.2 GiB free required

    The archive term is ``max``, not ``sum``: that IS the reclaim lever.  The
    export term is added to the archive term rather than max'd with it, so the
    figure is an upper bound that also covers re-exporting on top of a previous
    export while the last archive is still on disk.

    **The extrapolation is first-order and is known to UNDER-estimate.**  The
    ``× 1.32`` correction is anchored to the spec's own ~100 M row expectation,
    not to a measurement (RR × 357.8 gives 75.5 M fact rows; 100/75.5 = 1.32),
    and it is applied uniformly even though SP's ballot is broader than the
    national average.  The first real national number lands when this run
    finishes, which is exactly why the pre-flight exists.  Re-derive these
    constants from the measured national figures once `docs/baselines.md` has
    them.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from observatory import download, sources
from observatory.config import settings

__all__ = [
    "GIB",
    "HEADROOM",
    "InsufficientDisk",
    "NATIONAL_PEAK_BYTES",
    "SECTIONS_BY_UF",
    "free_bytes",
    "peak_bytes",
    "preflight",
    "reclaim_uf_archive",
    "uf_archive_bytes",
]

GIB = 1 << 30

#: The budget's headroom fraction: a run must fit with 20 % to spare.
HEADROOM = 0.20

#: Exterior sections.  Not a downloadable UF (`votacao_secao_2022_ZZ.zip` 404s)
#: — its rows live only in the national ``_BR`` members — but it *is* a real UF
#: in the warehouse, so it carries Postgres and export cost with no archive cost.
EXTERIOR = "ZZ"


# --------------------------------------------------------------------------
# Measured inputs (docs/baselines.md, Task 5, RR, 2026-08-04)
# --------------------------------------------------------------------------

#: ``QT_TOTAL_SECOES`` per UF, from `detalhe_votacao_munzona_2022_BR.csv`
#: (CD_ELEICAO 544, NR_TURNO 1, CD_CARGO 1, ST_VOTO_EM_TRANSITO = N), summed
#: over (municipality, zone).  Sums to 496,238 — the same national figure
#: `docs/baselines.md` extrapolates from, and RR's 1,387 matches it exactly.
SECTIONS_BY_UF: dict[str, int] = {
    "AC": 2_280, "AL": 6_820, "AM": 7_940, "AP": 1_837, "BA": 36_817,
    "CE": 24_837, "DF": 6_983, "ES": 9_620, "GO": 15_402, "MA": 19_493,
    "MG": 52_484, "MS": 7_149, "MT": 8_453, "PA": 19_707, "PB": 9_736,
    "PE": 21_422, "PI": 10_663, "PR": 25_875, "RJ": 36_550, "RN": 8_005,
    "RO": 4_791, "RR": 1_387, "RS": 27_429, "SC": 16_804, "SE": 5_841,
    "SP": 101_628, "TO": 4_088, EXTERIOR: 2_197,
}

NATIONAL_SECTIONS = sum(SECTIONS_BY_UF.values())  # 496,238

RR_SECTIONS = SECTIONS_BY_UF["RR"]
RR_POSTGRES_BYTES = 52_067_351   # pg_database_size('elections') after the RR load
RR_ARCHIVE_BYTES = 7_723_169     # data/raw/votacao_secao_2022_RR.zip
RR_EXPORT_BYTES = 371_749        # 15 shards; aggregates are a rounding error at RR scale

#: RR's ballot is unusually narrow, so scaling by section count alone
#: under-counts rows.  Anchored to the spec's ~100 M national fact-row
#: expectation against RR × 357.8 = 75.5 M.  See the module docstring.
BALLOT_BREADTH_CORRECTION = 1.32

#: The national archives every run needs and NO run may reclaim: A2's
#: `votacao_secao_2022_BR.zip` plus the four national rollup/detail archives.
#: Measured on disk after the A6 drop of the correspondence archives.
RAW_NATIONAL_BYTES = 1_100_164_493

#: WAL + system catalogs + `elections_test` in the Postgres volume, above
#: `pg_database_size('elections')`.  Bounded by `max_wal_size`, so it is a
#: constant, not a per-row cost.
CLUSTER_OVERHEAD_BYTES = 419_430_400  # 400 MiB


class InsufficientDisk(Exception):
    """A run was refused because it cannot fit on the target volume."""


# --------------------------------------------------------------------------
# Free space
# --------------------------------------------------------------------------


def free_bytes(path: Path | str) -> int:
    """Allocatable free bytes on the volume holding ``path``.

    Deliberately `shutil.disk_usage().free` and nothing cleverer — see the
    module docstring on macOS/APFS purgeable space.  Finder's "Available" is
    not this number and must not be used to decide whether a run can finish.
    """
    return shutil.disk_usage(Path(path)).free


def _gib(n: float) -> str:
    return f"{n / GIB:,.1f} GiB"


def preflight(required_bytes: int, path: Path | str | None = None) -> None:
    """Refuse the run unless ``path``'s volume has ``required_bytes`` + 20 %.

    Raises :class:`InsufficientDisk` with the three numbers an operator needs
    to act — what the run wants, what the disk has, and how much is missing —
    so nobody has to read this file to interpret the failure.
    """
    target = Path(path) if path is not None else settings.data_dir
    budget = required_bytes * (1 + HEADROOM)
    available = free_bytes(target)
    if available >= budget:
        return
    raise InsufficientDisk(
        f"not enough free disk on {target} to finish this run.\n"
        f"  estimated footprint : {_gib(required_bytes)}\n"
        f"  required (+{HEADROOM:.0%} headroom): {_gib(budget)}\n"
        f"  available           : {_gib(available)}\n"
        f"  shortfall           : {_gib(budget - available)}\n"
        "Free that much and re-run, or move data/raw/ or the Postgres volume "
        "to another disk (DATA_DIR / docker-compose volume). On macOS, "
        "'purgeable' space Finder counts as available is NOT allocatable — "
        "this gate reads the same figure as `df`."
    )


# --------------------------------------------------------------------------
# Footprint estimates
# --------------------------------------------------------------------------


def _scaled(measured_bytes: int, sections: int) -> float:
    """``measured_bytes`` (an RR figure) scaled to ``sections``, corrected."""
    return measured_bytes / RR_SECTIONS * sections * BALLOT_BREADTH_CORRECTION


def uf_archive_bytes(uf: str) -> int:
    """Estimated size of ``votacao_secao_2022_{uf}.zip``.  0 for the exterior."""
    key = uf.upper()
    if key == EXTERIOR:
        return 0  # exterior rows ship inside the national _BR archive
    return int(_scaled(RR_ARCHIVE_BYTES, SECTIONS_BY_UF[key]))


def peak_bytes(ufs: Iterable[str]) -> int:
    """Peak bytes a run over ``ufs`` occupies, with archives reclaimed as it goes.

    The per-UF archive term is the LARGEST single archive, not their sum —
    that is what :func:`reclaim_uf_archive` buys.  See the module docstring for
    the full arithmetic and for why this under-estimates.
    """
    keys = [uf.upper() for uf in ufs]
    unknown = sorted(set(keys) - set(SECTIONS_BY_UF))
    if unknown:
        raise KeyError(f"no section count for {', '.join(unknown)}")
    sections = sum(SECTIONS_BY_UF[key] for key in keys)
    largest_archive = max((uf_archive_bytes(key) for key in keys), default=0)
    return int(
        RAW_NATIONAL_BYTES
        + CLUSTER_OVERHEAD_BYTES
        + _scaled(RR_POSTGRES_BYTES, sections)
        + _scaled(RR_EXPORT_BYTES, sections)
        + largest_archive
    )


#: Peak footprint of the whole-country run — the number `make pipeline-all`
#: gates on.  All 27 states plus the exterior member.
NATIONAL_PEAK_BYTES = peak_bytes(SECTIONS_BY_UF)


# --------------------------------------------------------------------------
# Reclaiming per-UF archives
# --------------------------------------------------------------------------


def reclaim_uf_archive(uf: str) -> int:
    """Delete ``votacao_secao_2022_{uf}.zip`` and return the bytes freed.

    Returns 0 when the file is already gone, so re-running a completed national
    pass is a no-op rather than an error.

    ONLY that one file.  The national archives — above all
    `votacao_secao_2022_BR.zip`, which carries every presidential and exterior
    row (amendment A2) and is therefore read by EVERY state's ingest — must
    survive the whole run; deleting one after the first UF would surface hours
    later as a reconciliation gap, not as a missing file.  Passing ``"BR"``
    raises rather than doing that.

    RE-DOWNLOADABILITY.  Reclaiming trades disk for bandwidth, so the trade has
    to be honoured: a reclaimed archive must be genuinely re-fetched, never
    skipped.  Two things make that true, and only the second is ours:

    1. :func:`download._ensure_file` branches on ``dest.exists()`` *before* it
       consults `checksums.json`, so an absent file is always fetched —
       a recorded checksum alone cannot cause a skip.  (Worth stating because
       it is easy to assume the opposite from the module's "files whose
       recorded SHA-256 still matches are never re-fetched" contract.)
    2. We drop the file's `checksums.json` entry here anyway.  That manifest
       describes what is *on disk* — `_ensure_file` treats a present file with
       no entry as one to adopt — so an entry for a file that no longer exists
       is a lie, and leaving 27 of them behind after a national pass would
       mislead anyone auditing the raw directory.  It also means the invariant
       does not depend on the order of two checks inside another module.

    Reads ``download.RAW_DIR`` at call time, so tests can monkeypatch it.
    """
    key = uf.upper()
    if key not in sources.UFS:
        raise ValueError(
            f"{uf!r} is not a reclaimable per-UF archive. "
            f"Expected one of {', '.join(sources.UFS)}. "
            "'BR' in particular is a NATIONAL archive: every UF's ingest reads "
            "it for presidential and exterior rows (amendment A2)."
        )

    raw_dir = Path(download.RAW_DIR)  # read at call time — tests monkeypatch it
    name = sources.votes_section(key).filename
    assert name is not None  # every dataset carries a direct CDN url (A6)
    path = raw_dir / name

    freed = 0
    if path.exists():
        freed = path.stat().st_size
        path.unlink()

    checksums = download._load_checksums(raw_dir)
    if checksums.pop(name, None) is not None:
        download._save_checksums(raw_dir, checksums)

    return freed
