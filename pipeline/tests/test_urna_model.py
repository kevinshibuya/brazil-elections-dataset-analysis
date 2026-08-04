import pytest

from observatory.detectors import urna_model

pytestmark = pytest.mark.db


def test_geography_artifact_vanishes_within_municipality(test_db, seed_two_towns):
    # Two KINDS of town, many of each. Pro-Bolsonaro towns (.70) run mostly old
    # urnas; pro-Lula towns (.30) run mostly new ones. WITHIN every town the two
    # models poll identically up to symmetric noise, so the honest effect is 0
    # and the whole national gap is the geography.
    seed_two_towns(test_db)
    r = urna_model.UrnaModel().run(test_db)

    assert r.summary["n_municipalities"] >= 20        # enough clusters for a real CI
    assert abs(r.summary["raw_gap"]) > 0.10           # naive gap looks scary
    assert abs(r.summary["within_mun_gap"]) < 0.01    # honest gap ~ 0
    lo, hi = r.summary["ci95"]
    assert lo < 0 < hi                                # and indistinguishable from 0


def test_planted_true_model_effect_detected(test_db, seed_model_effect):
    seed_model_effect(test_db, effect=0.05)           # real within-town 5pt gap
    r = urna_model.UrnaModel().run(test_db)

    assert r.summary["within_mun_gap"] > 0.03
    assert r.summary["ci95"][0] > 0


def test_classified_on_round_2_model_not_round_1(test_db, seed_model_swap):
    # 1.1 % of real sections report a different CD_MODELO_URNA in round 2 than
    # in round 1, and some of those cross the pre2020/ue2020 line. The detector
    # reads PRES round 2, so round 2's model is the one that counts.
    seed_model_swap(test_db, swapped=30)              # pre2020 in r1, ue2020 in r2
    r = urna_model.UrnaModel().run(test_db)

    assert r.summary["model_sections"]["ue2020"] >= 30
    assert r.summary["basis"]["round"] == 2
