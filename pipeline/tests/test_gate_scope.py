"""The reconciliation gate must cost what a member costs, not what the warehouse holds.

Measured during the national run, per member (load time vs gate time):

    BA   load 1188s   gate  139s
    CE   load  757s   gate  209s
    DF   load  447s   gate  397s
    ES   load  433s   gate  460s
    GO   load  911s   gate  852s

The gate is independent of the member's own size — DF loaded 1.4 M rows and paid
397 s, BA loaded 5.6 M and paid 139 s — and grows with the whole warehouse,
because `run_gate` re-compares every municipality already loaded. At 10 of 28
members it had already overtaken the load and was doubling roughly every two
members; extrapolated over the remaining 18 it was ~12 hours of pure
re-checking of data that had already passed.

Re-checking earlier members buys nothing. Reconciliation keys on `mun_code`, and
a municipality belongs to exactly one UF, so both sides of every comparison come
from a single member's load. A state that reconciled when it landed cannot stop
reconciling because a later state arrived.

So the gate takes a scope. Fail-fast is preserved exactly: each member is still
gated as it lands, and a bad member still stops the pass at its own step.

WHAT MUST STAY NATIONAL. Two lookups deliberately search outside the member:
`_CAND_VALID_ELSEWHERE` (for PRES) and `_PARTY_VALID_ELSEWHERE`. They are what
separates a non-fatal `annulled_candidacy` from a FATAL `annulled_but_valid`
(amendment A4), and narrowing them to one UF would silently downgrade the fatal
category into a finding. Scoping the comparison is safe; scoping those is not.

Pre-authored by the orchestrator. Read-only to the implementer.
"""

from __future__ import annotations

import pytest

from observatory import reconcile


@pytest.mark.db
def test_scoped_gate_reports_only_the_scoped_member(test_db, seeded_two_uf):
    """A scoped run must not re-report municipalities from another member."""
    result = reconcile.run_gate(test_db, uf=seeded_two_uf.second, raise_on_failure=False)

    rows = reconcile.read_report(test_db)
    if rows.height:
        scoped = set(rows["mun_code"].to_list())
        assert scoped <= seeded_two_uf.municipalities[seeded_two_uf.second], (
            "a scoped gate reported a municipality outside its member"
        )
    assert result.ok


@pytest.mark.db
def test_scoped_gate_agrees_with_the_full_gate_for_that_member(test_db, seeded_two_uf):
    """Scoping is an optimisation, not a different verdict.

    The scoped run over a member must reach the same conclusion, and produce the
    same report rows for that member, as a full-warehouse run does.
    """
    uf = seeded_two_uf.second

    full = reconcile.run_gate(test_db, raise_on_failure=False)
    full_rows = reconcile.read_report(test_db)
    full_for_uf = full_rows.filter(
        full_rows["mun_code"].is_in(list(seeded_two_uf.municipalities[uf]))
    ).sort(["mun_code", "race", "round", "category", "key"])

    scoped = reconcile.run_gate(test_db, uf=uf, raise_on_failure=False)
    scoped_rows = reconcile.read_report(test_db).sort(
        ["mun_code", "race", "round", "category", "key"]
    )

    assert scoped.ok == full.ok
    assert scoped_rows.select(full_for_uf.columns).equals(full_for_uf)


@pytest.mark.db
def test_unscoped_gate_still_covers_the_whole_warehouse(test_db, seeded_two_uf):
    """The final national pass still gets a full run — that is what catches
    anything cross-member, including the A4 fatal category."""
    reconcile.run_gate(test_db, raise_on_failure=False)
    rows = reconcile.read_report(test_db)
    if rows.height:
        seen = set(rows["mun_code"].to_list())
        both = seeded_two_uf.municipalities[seeded_two_uf.first] | (
            seeded_two_uf.municipalities[seeded_two_uf.second]
        )
        assert seen <= both


@pytest.mark.db
def test_scoping_does_not_narrow_the_valid_elsewhere_lookups(test_db, seeded_two_uf):
    """A4's fatal branch depends on searching OUTSIDE the member.

    A candidacy that is annulled in this member but carries valid votes in
    another must still classify as fatal `annulled_but_valid` under a scoped
    run. If scoping narrowed that lookup, the fatal case would quietly become a
    non-fatal finding — the one regression that would make this optimisation
    unsafe.
    """
    seeded_two_uf.plant_annulled_but_valid_across_members()

    result = reconcile.run_gate(test_db, uf=seeded_two_uf.second, raise_on_failure=False)

    assert not result.ok, "scoped gate missed a cross-member annulled_but_valid"
    categories = {c.name for c in result.categories if c.fatal and c.rows}
    assert "annulled_but_valid" in categories
