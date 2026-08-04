import pytest
from observatory import ingest

pytestmark = pytest.mark.db


def test_atomic_swap_preserves_previous_state(test_db, fixture_batch_ok, fixture_batch_poison):
    ingest.load_batch(test_db, fixture_batch_ok)          # good load
    before = ingest.counts(test_db)
    with pytest.raises(ingest.QuarantineGateError):
        ingest.load_batch(test_db, fixture_batch_poison)  # >0.01% bad rows
    assert ingest.counts(test_db) == before               # swap never happened


def test_quarantine_rows_carry_reason(test_db, fixture_batch_one_bad):
    # fixture contract: >= 10,000 attempted rows with exactly one bad -> stays under the 0.01% gate
    report = ingest.load_batch(test_db, fixture_batch_one_bad)
    assert report.rows_attempted >= 10_000
    assert report.rows_quarantined == 1
    q = ingest.read_quarantine(test_db)
    assert q.height == 1 and q["reason"][0] != ""


def test_supplementary_2026_election_is_filtered_out(test_db, fixture_batch_mixed_elections):
    # AMENDED A3: CD_ELEICAO=6278 "Eleicao Suplementar Governador 2026" is mixed into
    # the 2022 archives and collides on (uf, mun, zone, section, race, round).
    # Ingest must keep only the 2022 general (544/545/546/547).
    ingest.load_batch(test_db, fixture_batch_mixed_elections)
    assert ingest.distinct_election_ids(test_db) <= {544, 545, 546, 547}
    assert ingest.counts(test_db)["fact_votes"] > 0
