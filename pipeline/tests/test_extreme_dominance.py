import pytest
from observatory.detectors import extreme_dominance as ed

pytestmark = pytest.mark.db


def test_planted_shutout_ranks_first(test_db, seed_votes):
    # 40 sections ~55% for cand 13 (n≈300 each), one section 300/300
    seed_votes(test_db, baseline_share=0.55, sections=40, n=300, planted=(300, 300))
    ed.ExtremeDominance().run(test_db)
    top = ed.read_scores(test_db).sort("score", descending=True)
    assert top["detail"][0]["k"] == 300 and top["detail"][0]["n"] == 300
    assert top["score"][0] > top["score"][1] * 3          # planted section dominates


def test_clean_overdispersed_municipality_no_extreme_flags(test_db, seed_votes):
    # natural spread: shares drawn 0.35–0.75 across sections — must NOT alarm
    seed_votes(test_db, share_range=(0.35, 0.75), sections=60, n=250, planted=None)
    ed.ExtremeDominance().run(test_db)
    assert ed.read_scores(test_db)["score"].max() < 4.0    # no p < 1e-4


def test_small_municipality_uses_state_fallback(test_db, seed_votes):
    seed_votes(test_db, sections=3, n=200, with_state_context=True)
    ed.ExtremeDominance().run(test_db)
    assert set(ed.read_scores(test_db)["detail"].struct.field("baseline").unique()) == {"state"}
