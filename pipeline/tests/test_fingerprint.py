import pytest

from observatory.detectors import fingerprint

pytestmark = pytest.mark.db


def test_grid_shape_and_mass_conservation(test_db, seed_state):
    seed_state(test_db, sections=500)
    r = fingerprint.Fingerprint().run(test_db)
    grid = r.state_payloads["RR"]["grid"]
    assert len(grid) == 100 and len(grid[0]) == 100
    assert sum(map(sum, grid)) == 500


def test_planted_corner_smear_raises_corner_mass(test_db, seed_state):
    seed_state(test_db, sections=500, corner_planted=50)   # turnout>95%, share>95%
    r = fingerprint.Fingerprint().run(test_db)
    assert r.state_payloads["RR"]["corner_mass"] >= 50 / 550


def test_no_per_section_scores_written(test_db, seed_state):
    # distribution-level detector: sections_scored = 0, composite ignores it
    seed_state(test_db, sections=500)
    fingerprint.Fingerprint().run(test_db)
    assert fingerprint.read_scores(test_db).height == 0
