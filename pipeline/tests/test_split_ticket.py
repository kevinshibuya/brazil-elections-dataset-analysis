import pytest

from observatory.detectors import split_ticket
from observatory.detectors.split_ticket import read_scores

pytestmark = pytest.mark.db


def test_uniform_alliance_pattern_not_flagged(test_db, seed_split):
    # every section: pres share .70, coalition-deputy share .30 — big gap, but uniform
    seed_split(test_db, pres=0.70, dep=0.30, sections=50, noise=0.02)
    split_ticket.SplitTicket().run(test_db)
    assert read_scores(test_db, "split_ticket")["score"].max() < 2.0


def test_single_divergent_section_ranks_first(test_db, seed_split):
    seed_split(test_db, pres=0.55, dep=0.50, sections=50, noise=0.02,
               planted=dict(pres=0.98, dep=0.10))
    split_ticket.SplitTicket().run(test_db)
    top = read_scores(test_db, "split_ticket").sort("score", descending=True)
    assert top["detail"][0]["z"] > 5 and top["score"][0] > 2 * top["score"][1]
