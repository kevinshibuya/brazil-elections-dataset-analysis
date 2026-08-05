"""The national driver's load plan: exactly one reset, then appends.

`02_ingest.py` truncates the whole warehouse by default.  The driver runs it
once per member, so if more than one of those invocations resets, every member
before it is destroyed -- a national run would finish holding only the LAST
state, `03_analyze.py` would score that one state, and 27 exports would find
nothing.  Nothing downstream would notice: the reconciliation gate runs after
each load against a warehouse that IS internally consistent, because it
contains exactly the one member just written.

These assertions are on the plan (`build_actions`), which is cheap.  The claim
that the plan's `--append` actually works against real Postgres is
`test_two_uf_append.py`, and it is deliberately not stubbed -- a stubbed
`subprocess.run` cannot see a TRUNCATE.
"""

from __future__ import annotations

import importlib

import pytest

pipeline_all = importlib.import_module("05_pipeline_all")


def _ingests(actions) -> list[tuple[str, ...]]:
    return [a.argv for a in actions if a.argv and a.argv[0] == "02_ingest.py"]


def _members(actions) -> list[str]:
    return [argv[argv.index("--uf") + 1] for argv in _ingests(actions)]


# ---------------------------------------------------------------------------
# One reset, then appends
# ---------------------------------------------------------------------------


def test_only_the_first_ingest_resets_the_warehouse():
    """The defect this file exists for: N resets would keep only the last UF."""
    actions = pipeline_all.build_actions(["ZZ", "AC", "AP", "RR"])
    ingests = _ingests(actions)

    assert len(ingests) == 4
    resetting = [argv for argv in ingests if "--append" not in argv]
    assert len(resetting) == 1, (
        f"exactly one ingest may truncate the warehouse; got {len(resetting)}: "
        f"{resetting}"
    )
    assert resetting[0] == ingests[0], "the resetting ingest must be the first"
    assert all("--append" in argv for argv in ingests[1:])
    assert _members(actions) == ["ZZ", "AC", "AP", "RR"]


def test_a_single_member_run_still_resets():
    """`--only RR` is a replace, not an append onto whatever was there."""
    ingests = _ingests(pipeline_all.build_actions(["RR"]))
    assert len(ingests) == 1
    assert "--append" not in ingests[0]


def test_batch_size_reaches_every_ingest_including_the_appending_ones():
    ingests = _ingests(pipeline_all.build_actions(["ZZ", "AC"], batch_size=1234))
    assert all(("--batch-size", "1234") == argv[-2:] for argv in ingests)
    assert "--append" in ingests[1]


# ---------------------------------------------------------------------------
# Resuming
# ---------------------------------------------------------------------------


def test_resume_never_resets_and_loads_only_the_tail():
    """Resuming onto a half-loaded warehouse must not truncate it."""
    actions = pipeline_all.build_actions(
        ["ZZ", "AC", "AP", "RR"], load_ufs=["AP", "RR"], reset=False
    )
    ingests = _ingests(actions)
    assert _members(actions) == ["AP", "RR"]
    assert all("--append" in argv for argv in ingests), ingests


def test_resume_still_exports_every_member_not_just_the_resumed_tail():
    """The skipped members were never exported -- the run died before export.

    Exporting only the tail would leave the states loaded before the failure
    without shards, which is the same class of silent hole as the reset bug.
    """
    actions = pipeline_all.build_actions(
        ["ZZ", "AC", "AP", "RR"], load_ufs=["AP", "RR"], reset=False
    )
    exported = [
        a.argv[a.argv.index("--uf") + 1]
        for a in actions
        if a.argv and a.argv[0] == "04_export.py" and "--shards-only" in a.argv
    ]
    assert exported == ["ZZ", "AC", "AP", "RR"]


def test_reclaim_still_follows_every_appending_ingest():
    """The storage lever must survive the append change."""
    actions = pipeline_all.build_actions(["ZZ", "AC", "AP"])
    order = [(a.kind, a.uf) for a in actions if a.kind in {"script", "reclaim"}]
    # ZZ has no archive to reclaim; AC and AP each get one, after their ingest.
    reclaimed = [uf for kind, uf in order if kind == "reclaim"]
    assert reclaimed == ["AC", "AP"]
    for uf in ("AC", "AP"):
        ingest_at = next(
            i for i, a in enumerate(actions)
            if a.argv and a.argv[0] == "02_ingest.py" and uf in a.argv
        )
        reclaim_at = next(
            i for i, a in enumerate(actions) if a.kind == "reclaim" and a.uf == uf
        )
        assert reclaim_at > ingest_at, f"{uf} reclaimed before it was ingested"


# ---------------------------------------------------------------------------
# The CLI wiring
# ---------------------------------------------------------------------------


def test_resume_from_an_unselected_member_is_refused(capsys):
    with pytest.raises(SystemExit):
        pipeline_all.main(["--only", "RR,ZZ", "--resume-from", "SP", "--dry-run"])
    assert "cannot resume from SP" in capsys.readouterr().err


def test_dry_run_prints_the_append_flags_it_would_use(capsys):
    assert pipeline_all.main(["--only", "ZZ,AC,AP", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "02_ingest.py --uf ZZ\n" in out or "--uf ZZ " in out
    assert out.count("--append") == 2, out
