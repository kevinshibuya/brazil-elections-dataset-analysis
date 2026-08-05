"""Two REAL states, loaded back to back, must both survive.

This is the test that would have caught the reset defect, and it is the reason
it is written the expensive way.  `02_ingest.py` TRUNCATEs the whole warehouse
unless told not to, and the national driver runs it once per member.  A test
that stubs `subprocess.run` sees the argv and nothing else -- it cannot see a
TRUNCATE, so it certifies a driver that would finish a 28-member run holding
one state.  Nothing downstream would object either: the reconciliation gate
runs after each load against a warehouse that IS internally consistent,
because it contains exactly the one member just written.

So: no stubs, no fake archives.  Real `02_ingest.py` as a real subprocess, real
TSE archives, real Postgres, two of the smallest states (AC 2,280 sections and
AP 1,837, against RR's 1,387), and the assertion is that the second load leaves
the first one *byte for byte* where it was.

WHICH DATABASE.  `elections_test` -- the same throwaway database the rest of the
`db` suite uses, via `$DATABASE_URL` on the subprocess.  The real `elections`
warehouse is never opened, let alone truncated: it holds a loaded state that
costs ~5 minutes per UF to rebuild.

COST.  Two full ingests, each of which scans the national `_BR` members for its
own state's presidential rows (amendment A2) -- that scan, not the state's own
7 MB archive, is 79 % of the wall clock.  Hence `integration`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import conftest
from observatory import download, ingest, reconcile, sources, storage
from observatory.db import Database

pytestmark = pytest.mark.integration

PIPELINE_DIR = Path(__file__).resolve().parents[1]

#: Two of the smallest per-UF archives, loaded in this order: the FIRST resets
#: the warehouse, the SECOND appends into it.  That is exactly the shape
#: `05_pipeline_all.py` drives.
FIRST, SECOND = "AC", "AP"

#: Loaded sections land BELOW TSE's published `QT_TOTAL_SECOES` (RR: 1,268
#: loaded against 1,387 published = 91 %), so the plausibility band is one-sided
#: and generous.  It is here to catch "zero rows" and "the wrong state", not to
#: pin a number -- pinning one would make this test a second, worse copy of the
#: reconciliation gate.
SECTION_BAND = (0.60, 1.05)


@dataclass(frozen=True)
class Snapshot:
    """Per-UF row counts, the only thing these assertions need."""

    votes: dict[str, tuple[int, int]]      # uf -> (fact_votes rows, total votes)
    sections: dict[str, int]               # uf -> dim_section rows
    stats: dict[str, int]                  # uf -> fact_section_stats rows


def _snapshot(db: Database) -> Snapshot:
    return Snapshot(
        votes={
            str(uf): (int(rows), int(votes))
            for uf, rows, votes in db.fetch(
                "SELECT s.uf, count(*), COALESCE(SUM(f.votes), 0) "
                "FROM fact_votes f JOIN dim_section s ON s.id = f.section_id "
                "GROUP BY 1"
            )
        },
        sections={
            str(uf): int(n)
            for uf, n in db.fetch("SELECT uf, count(*) FROM dim_section GROUP BY 1")
        },
        stats={
            str(uf): int(n)
            for uf, n in db.fetch(
                "SELECT s.uf, count(*) FROM fact_section_stats f "
                "JOIN dim_section s ON s.id = f.section_id GROUP BY 1"
            )
        },
    )


def _ingest(uf: str, *flags: str) -> subprocess.CompletedProcess[str]:
    """Run the REAL `02_ingest.py`, pointed at `elections_test`."""
    env = {**os.environ, "DATABASE_URL": conftest.test_database_url()}
    return subprocess.run(  # noqa: S603
        [sys.executable, "02_ingest.py", "--uf", uf, *flags],
        cwd=PIPELINE_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def _require_ok(done: subprocess.CompletedProcess[str], what: str) -> None:
    if done.returncode == 0:
        return
    raise AssertionError(
        f"{what} exited {done.returncode}\n"
        f"--- stdout (tail) ---\n{done.stdout[-4000:]}\n"
        f"--- stderr ---\n{done.stderr[-4000:]}"
    )


@pytest.fixture(scope="module")
def two_uf_load():
    """Load FIRST (resetting), snapshot, append SECOND, snapshot.

    Module-scoped: the loads cost minutes, the assertions cost milliseconds.
    """
    reason = conftest._ensure_test_database()
    if reason:
        pytest.skip(reason)

    # The national archives are ~1 GiB and are NOT downloaded by a test.
    missing = [
        d.filename
        for d in sources.national_datasets()
        if d.filename and not (download.RAW_DIR / d.filename).exists()
    ]
    if missing:
        pytest.skip(
            f"national archives missing from {download.RAW_DIR}: "
            f"{', '.join(missing)} — run `make pipeline UF=RR` first."
        )

    for uf in (FIRST, SECOND):
        download.fetch_report([sources.votes_section(uf)])

    db = Database.connect(conftest.test_database_url())
    db.create_schema()
    try:
        _require_ok(_ingest(FIRST), f"02_ingest.py --uf {FIRST}")
        after_first = _snapshot(db)

        _require_ok(_ingest(SECOND, "--append"), f"02_ingest.py --uf {SECOND} --append")
        after_second = _snapshot(db)

        yield after_first, after_second, db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


def test_the_first_state_is_still_there_after_the_second_is_appended(two_uf_load):
    """THE assertion.  Without `--append` this is 0 rows and the run is a lie."""
    after_first, after_second, _db = two_uf_load

    assert FIRST in after_second.votes, (
        f"{FIRST} vanished when {SECOND} was loaded — the second ingest "
        f"truncated the warehouse. Present: {sorted(after_second.votes)}"
    )
    assert after_second.votes[FIRST] == after_first.votes[FIRST], (
        f"{FIRST} changed while {SECOND} was appended: "
        f"{after_first.votes[FIRST]} -> {after_second.votes[FIRST]}"
    )
    assert after_second.sections[FIRST] == after_first.sections[FIRST]
    assert after_second.stats[FIRST] == after_first.stats[FIRST]


def test_the_second_state_actually_loaded(two_uf_load):
    """An append that appends nothing would satisfy the test above."""
    after_first, after_second, _db = two_uf_load

    assert SECOND not in after_first.votes, (
        f"{SECOND} was already present before its own load — the fixture did "
        "not start from a clean warehouse, so nothing here proves anything."
    )
    rows, votes = after_second.votes[SECOND]
    assert rows > 0 and votes > 0
    assert after_second.sections[SECOND] > 0
    assert after_second.stats[SECOND] > 0


def test_the_warehouse_holds_exactly_the_two_states_loaded(two_uf_load):
    """No third UF leaked in through the national `_BR` members (A2)."""
    _after_first, after_second, _db = two_uf_load
    assert sorted(after_second.votes) == sorted([FIRST, SECOND])
    assert sorted(after_second.sections) == sorted([FIRST, SECOND])


def test_per_uf_section_counts_are_plausible(two_uf_load):
    """Each state carries its OWN sections, in the right order of magnitude."""
    _after_first, after_second, _db = two_uf_load
    low, high = SECTION_BAND
    for uf in (FIRST, SECOND):
        published = storage.SECTIONS_BY_UF[uf]
        loaded = after_second.sections[uf]
        assert low * published <= loaded <= high * published, (
            f"{uf}: loaded {loaded:,} sections against TSE's published "
            f"{published:,} — outside the {low:.0%}–{high:.0%} band"
        )
    # AC is the larger of the two by TSE's own count; the load must agree.
    assert after_second.sections[FIRST] > after_second.sections[SECOND]


# ---------------------------------------------------------------------------
# What the gates mean once the warehouse holds more than one state
# ---------------------------------------------------------------------------


def test_reconciliation_passes_over_a_two_state_warehouse(two_uf_load):
    """Every comparison keys on `mun_code`, which belongs to exactly one UF.

    So the gate is per-municipality and additive: a second state adds keys
    rather than netting out against the first. If that were not so, a state
    that loaded badly could be masked by one that loaded well.
    """
    _after_first, _after_second, db = two_uf_load
    result = reconcile.run_gate(db, raise_on_failure=False)
    assert result.ok, result.summary()


def test_reconciliation_report_covers_both_states(two_uf_load):
    """The findings are per-UF, not collapsed into a national total."""
    _after_first, _after_second, db = two_uf_load
    reconcile.run_gate(db, raise_on_failure=False)
    ufs = {
        str(uf)
        for (uf,) in db.fetch(
            f"SELECT DISTINCT m.uf FROM {reconcile.REPORT_TABLE} r "
            f"JOIN dim_municipality m ON m.code = r.mun_code"
        )
    }
    # Both states have annulled candidacies and rollup disagreements of their
    # own; neither may be absent, which is what a UF-blind report would look
    # like.
    assert ufs == {FIRST, SECOND}, ufs


def test_the_quarantine_gate_is_scored_per_run_not_over_the_warehouse(two_uf_load):
    """Each load is judged on its own attempted rows.

    That is the invariant append must not break: scoring the second run's bad
    rows against both runs' attempted rows would halve the measured rate and
    quietly widen the 0.01 % gate with every state added.
    """
    _after_first, _after_second, db = two_uf_load
    stamps = ingest.read_gate_runs(db, "quarantine")   # newest first
    assert stamps.height >= 2
    second, first = (
        int(detail.split("attempted=")[1].split()[0].replace(",", ""))
        for detail in stamps["detail"][:2]
    )
    assert first > 0 and second > 0
    # Both runs are dominated by the same national `_BR` scan (A2), so their
    # attempted counts sit within a factor of two of each other. A denominator
    # taken over the WAREHOUSE would make the second roughly the sum of both.
    assert 0.5 < second / first < 2.0, (
        f"attempted: {first:,} then {second:,} — the second run's denominator "
        "looks cumulative, which would silently widen the 0.01 % gate with "
        "every state added"
    )


def test_appending_a_state_already_loaded_fails_loudly(two_uf_load):
    """`fact_votes` has no ON CONFLICT (A3) — a double load must not be quiet.

    This is the guard rail under `--resume-from`: resuming onto a member that
    is already in the warehouse has to raise, not double-count.
    """
    _after_first, after_second, db = two_uf_load
    done = _ingest(SECOND, "--append")
    assert done.returncode != 0, (
        f"re-appending {SECOND} succeeded — it should have raised a duplicate "
        f"key.\n{done.stdout[-2000:]}"
    )
    assert "duplicate" in (done.stdout + done.stderr).lower()
    # ... and left the warehouse exactly as it was: the run is one transaction.
    assert _snapshot(db).votes == after_second.votes
