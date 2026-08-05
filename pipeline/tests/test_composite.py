import pytest

from observatory.detectors import composite
from observatory.detectors.composite import read_scores

pytestmark = pytest.mark.db


def test_composite_is_mean_of_mean_rank_percentiles(test_db, seed_multi_detector):
    seed_multi_detector(test_db)          # fixed, known score vectors across 3 detectors
    composite.Composite().run(test_db)
    row = read_scores(test_db, "composite").sort("score", descending=True).head(1)
    d = row["detail"][0]
    assert d["n_detectors"] == len(d["percentiles"])
    assert abs(row["score"][0] - sum(d["percentiles"].values()) / d["n_detectors"]) < 1e-9


def test_fewer_than_two_contributors_writes_no_row(test_db, seed_single_detector):
    seed_single_detector(test_db)
    composite.Composite().run(test_db)
    assert read_scores(test_db, "composite").height == 0


def test_planted_hot_zone_survives_bh(test_db, seed_zones):
    # 50 zones x 40 sections = 2 000 sections, so the national high set is the
    # top ceil(0.01 * 2000) = 20 by composite. Highs placed DETERMINISTICALLY
    # (no sampling): 10 in the hot zone, 10 spread one-per-zone over zones 0-9.
    seed_zones(test_db, zones=50, per_zone=40, hot_zone_high=10, spread_high=10)
    composite.Composite().run(test_db)
    surviving = composite.read_clusters(test_db)
    assert len(surviving) == 1 and surviving[0]["observed_high"] == 10


def test_evenly_spread_highs_yield_no_clusters(test_db, seed_zones):
    # 20 highs placed one-per-zone across 20 different zones -- deterministic,
    # no hot spot, so nothing may survive Benjamini-Hochberg at q = 0.05.
    seed_zones(test_db, zones=50, per_zone=40, hot_zone_high=0, spread_high=20)
    composite.Composite().run(test_db)
    assert composite.read_clusters(test_db) == []
