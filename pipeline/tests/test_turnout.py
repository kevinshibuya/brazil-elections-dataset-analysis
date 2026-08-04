import pytest

from observatory.detectors import turnout
from observatory.detectors.turnout import read_scores

pytestmark = pytest.mark.db


def test_both_extremes_flagged(test_db, seed_turnout):
    seed_turnout(test_db, base=0.80, noise=0.02, sections=60,
                 planted=[0.999, 0.35])                     # one high, one low
    turnout.Turnout().run(test_db)
    top2 = read_scores(test_db, "turnout").sort("score", descending=True).head(2)
    zs = [d["z"] for d in top2["detail"]]
    assert max(zs) > 4 and min(zs) < -4


def test_zero_mad_falls_back_to_state(test_db, seed_turnout):
    seed_turnout(test_db, constant=0.80, sections=20, with_state_context=True)
    turnout.Turnout().run(test_db)
    assert "state" in set(read_scores(test_db, "turnout")["detail"].struct.field("baseline"))
