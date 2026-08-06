import pytest
from observatory import reconcile


def test_mismatch_writes_full_report_then_raises(test_db, seeded_mismatch):
    with pytest.raises(reconcile.ReconciliationError):
        reconcile.run_gate(test_db)
    rep = reconcile.read_report(test_db)
    assert rep.height == seeded_mismatch.expected_diffs   # ALL diffs, not just first
    assert {"category", "ours", "theirs", "diff"} <= set(rep.columns)


def test_annulled_candidacy_is_reported_but_not_fatal(test_db, seeded_annulled_candidacy):
    # AMENDED A4: a candidate with section votes but NO rollup row is an annulled
    # candidacy -- recorded as its own category, non-fatal, never silently dropped.
    result = reconcile.run_gate(test_db)
    assert result.ok
    rep = reconcile.read_report(test_db)
    ann = rep.filter(rep["category"] == "annulled_candidacy")
    assert ann.height == seeded_annulled_candidacy.expected_candidates
    assert ann["ours"].sum() == seeded_annulled_candidacy.expected_votes
    assert ann["theirs"].is_null().all()          # absent from the rollup entirely


def test_annulled_votes_appearing_as_valid_hard_fails(test_db, seeded_annulled_but_valid):
    # AMENDED A4: the one condition under which category 4 IS fatal.
    with pytest.raises(reconcile.ReconciliationError):
        reconcile.run_gate(test_db)


@pytest.mark.integration
def test_rr_real_data_reconciles(real_db):
    """Zero mismatches on the real RR ingest.

    SCOPED TO RR ON PURPOSE, and it has to be. This called `run_gate(real_db)`
    unscoped, which was the same thing back when `elections` held Roraima and
    nothing else. After Task 23 that database holds all 28 members — 472,075
    sections, 73 M vote rows — so the unscoped call stopped meaning "check RR"
    and became the full national gate, the exact cost the scoping change removed
    from the pipeline (measured: 2,196 s for one of its five checks at 25 M
    rows, and this warehouse is ~3x that). It ran for over 30 minutes without
    finishing.

    Scoping restores what the test always claimed to assert. National coverage
    is not lost: `05_pipeline_all.py` gates every member as it lands, and all 28
    passed.
    """
    assert reconcile.run_gate(real_db, uf="RR").ok
