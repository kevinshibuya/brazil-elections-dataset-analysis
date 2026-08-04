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
    assert reconcile.run_gate(real_db).ok    # zero mismatches on real RR ingest
